import httpx
import pytest

from app.services import chat_service


async def _register_and_login(
    client: httpx.AsyncClient,
    username: str,
) -> dict[str, str]:
    register_response = await client.post(
        "/auth/register",
        json={"username": username, "password": "88888888"},
    )
    assert register_response.status_code == 201

    login_response = await client.post(
        "/auth/login",
        data={"username": username, "password": "88888888"},
    )
    assert login_response.status_code == 200

    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


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


async def test_users_cannot_read_each_others_conversations(client):
    owner_headers = await _register_and_login(client, "owner")
    other_headers = await _register_and_login(client, "other")

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
    monkeypatch,
    path,
):
    async def llm_must_not_run(*args, **kwargs):
        raise AssertionError("越权请求不应该调用 LLM")

    monkeypatch.setattr(chat_service, "call_llm", llm_must_not_run)
    monkeypatch.setattr(chat_service, "stream_llm", llm_must_not_run)

    owner_headers = await _register_and_login(client, "chat-owner")
    other_headers = await _register_and_login(client, "chat-other")

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
