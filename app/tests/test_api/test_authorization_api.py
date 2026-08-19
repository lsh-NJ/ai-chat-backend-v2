import pytest

from app.core.security import create_access_token
from app.db.session import AsyncSessionFactory
from app.models.user import User
from app.services import chat_service


async def _create_user_headers(
    create_test_user,
    username: str,
) -> dict[str, str]:
    user = await create_test_user(username)
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("GET", "/conversations", None),
        ("POST", "/conversations", {"title": "未认证"}),
        ("GET", "/conversations/1/messages", None),
        ("POST", "/chat", {"message": "未认证"}),
        ("POST", "/chat/stream", {"message": "未认证"}),
    ],
)
async def test_resource_endpoints_require_token(client, method, path, json_body):
    response = await client.request(method, path, json=json_body)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_invalid_token_returns_401(client):
    response = await client.get(
        "/conversations",
        headers={"Authorization": "Bearer forged-token"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_inactive_user_token_returns_401(client):
    async with AsyncSessionFactory() as session:
        user = User(
            username="inactive-token",
            password_hash="unused-valid-test-hash",
            is_active=False,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    response = await client.get(
        "/conversations",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_users_cannot_read_each_others_conversations(client, create_test_user):
    owner_headers = await _create_user_headers(create_test_user, "owner")
    other_headers = await _create_user_headers(create_test_user, "other")

    owner_conversation = await client.post(
        "/conversations",
        json={"title": "owner secret"},
        headers=owner_headers,
    )
    assert owner_conversation.status_code == 200
    conversation_id = owner_conversation.json()["id"]

    other_list = await client.get("/conversations", headers=other_headers)
    assert other_list.status_code == 200
    assert other_list.json() == []

    other_history = await client.get(
        f"/conversations/{conversation_id}/messages",
        headers=other_headers,
    )
    assert other_history.status_code == 404


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
async def test_users_cannot_chat_in_each_others_conversations(
    client,
    create_test_user,
    monkeypatch,
    path,
):
    async def llm_must_not_run(*args, **kwargs):
        raise AssertionError("越权请求不应该调用 LLM")

    monkeypatch.setattr(chat_service, "call_llm", llm_must_not_run)
    monkeypatch.setattr(chat_service, "stream_llm", llm_must_not_run)

    owner_headers = await _create_user_headers(create_test_user, "chat-owner")
    other_headers = await _create_user_headers(create_test_user, "chat-other")

    owner_conversation = await client.post(
        "/conversations",
        json={"title": "owner chat"},
        headers=owner_headers,
    )
    assert owner_conversation.status_code == 200
    conversation_id = owner_conversation.json()["id"]

    response = await client.post(
        path,
        json={"conversation_id": conversation_id, "message": "越权消息"},
        headers=other_headers,
    )

    assert response.status_code == 404
