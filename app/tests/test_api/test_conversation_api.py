import httpx
import pytest

from app.main import app
from app.db.session import AsyncSessionFactory, get_db
from app.models.conversation import Conversation
from app.repositories.message_repository import MessageRepository


@pytest.fixture
async def client(fresh_schema):
    async def override_get_db():
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as test_client:
            yield test_client
    app.dependency_overrides.clear()


async def _create_conversation(client, title: str | None = None) -> dict:
    response = await client.post("/conversations", json={"title": title})
    assert response.status_code == 200
    return response.json()


# 目标：POST /conversations 创建会话成功路径，数据真实落库
async def test_create_conversation(client):
    body = await _create_conversation(client, title="会话 1")

    assert body["id"] > 0
    assert body["title"] == "会话 1"
    assert body["created_at"] is not None

    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, body["id"])
        assert conversation is not None
        assert conversation.title == "会话 1"


# 目标：GET /conversations 初始为空列表
async def test_list_conversations_is_empty_initially(client):
    response = await client.get("/conversations")

    assert response.status_code == 200
    assert response.json() == []


# 目标：创建会话后，GET /conversations 能查到它
async def test_list_conversations_returns_created(client):
    created = await _create_conversation(client, title="会话 1")

    response = await client.get("/conversations")
    assert response.status_code == 200

    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == created["id"]
    assert body[0]["title"] == "会话 1"


# 目标：GET /conversations/{id}/messages，会话不存在 → 404
async def test_history_missing_conversation_returns_404(client):
    response = await client.get("/conversations/999999/messages")

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


# 目标：GET /conversations/{id}/messages 按 id 升序返回全部消息
async def test_history_returns_messages_in_order(client):
    conversation = await _create_conversation(client, title="历史会话")
    conversation_id = conversation["id"]

    # 测试的 arrange 阶段：直接用 Repository 造数据，不走 LLM
    async with AsyncSessionFactory() as session:
        repo = MessageRepository(session)
        await repo.add(
            conversation_id=conversation_id,
            role="user",
            content="第一条",
        )
        await repo.add(
            conversation_id=conversation_id,
            role="assistant",
            content="第二条",
        )
        await session.commit()

    response = await client.get(f"/conversations/{conversation_id}/messages")
    assert response.status_code == 200

    body = response.json()
    assert [(m["role"], m["content"]) for m in body] == [
        ("user", "第一条"),
        ("assistant", "第二条"),
    ]
