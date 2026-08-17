import httpx
from app.db.session import AsyncSessionFactory
from app.models.conversation import Conversation
from app.repositories.message_repository import MessageRepository

async def _create_conversation(client, header: dict[str, str],title: str | None = None) -> dict:
    response = await client.post(
        "/conversations",
        json={"title": title},
        headers = header,
    )
    assert response.status_code == 200
    return response.json()


async def test_create_conversation(client, auth_headers):
    body = await _create_conversation(client, auth_headers, title="会话 1")

    assert body["id"] > 0
    assert body["title"] == "会话 1"
    assert body["created_at"] is not None

    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, body["id"])
        assert conversation is not None
        assert conversation.title == "会话 1"


# 目标：GET /conversations 初始为空列表
async def test_list_conversations_is_empty_initially(client, auth_headers):
    response = await client.get("/conversations", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == []


# 目标：创建会话后，GET /conversations 能查到它
async def test_list_conversations_returns_created(client, auth_headers):
    created = await _create_conversation(client, auth_headers, title="会话 1")

    response = await client.get("/conversations", headers=auth_headers)
    assert response.status_code == 200

    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == created["id"]
    assert body[0]["title"] == "会话 1"


# 目标：GET /conversations/{id}/messages，会话不存在 → 404
async def test_history_missing_conversation_returns_404(client, auth_headers):
    response = await client.get(
        "/conversations/999999/messages",
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


# 目标：GET /conversations/{id}/messages 按 id 升序返回全部消息
async def test_history_returns_messages_in_order(client, auth_headers):
    conversation = await _create_conversation(client, auth_headers, title="历史会话")
    conversation_id = conversation["id"]

    # 测试的 arrange 阶段：直接用 Repository 造数据，不走 LLM
    async with AsyncSessionFactory() as session:
        repo = MessageRepository(session)
        await repo.add(
            conversation_id=conversation_id,
            role="user",
            content="第一条",
            is_complete=True,
        )
        await repo.add(
            conversation_id=conversation_id,
            role="assistant",
            content="第二条",
            is_complete=True,
        )
        await session.commit()

    response = await client.get(f"/conversations/{conversation_id}/messages", headers=auth_headers)
    assert response.status_code == 200

    body = response.json()
    assert [(m["role"], m["content"]) for m in body] == [
        ("user", "第一条"),
        ("assistant", "第二条"),
    ]
    assert [message["is_complete"] for message in body] == [True, True]
