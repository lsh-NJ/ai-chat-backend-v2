import httpx
import pytest
from sqlalchemy import select

from app.core.exceptions import (
    LLMConfigurationError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.db.session import AsyncSessionFactory, get_db
from app.main import app
from app.models.conversation import Conversation
from app.models.message import Message
from app.services import chat_service


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


async def _messages_of(session, conversation_id: int) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list(result.scalars().all())


async def test_chat_nonexistent_conversation_returns_404(client, monkeypatch):
    async def fake_call_llm(client, messages):
        raise AssertionError("不存在的会话不应该走到 LLM 调用")

    monkeypatch.setattr(chat_service, "call_llm", fake_call_llm)

    response = await client.post(
        "/chat",
        json={"conversation_id": 999999, "message": "你好"},
    )

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


async def test_history_missing_conversation_returns_404(client):
    response = await client.get("/conversations/999999/messages")

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


async def test_chat_llm_timeout_returns_504(client, monkeypatch):
    async def fake_timeout(client, messages):
        raise LLMTimeoutError("LLM request timeout")

    monkeypatch.setattr(chat_service, "call_llm", fake_timeout)

    response = await client.post("/chat", json={"message": "你好"})

    assert response.status_code == 504
    assert "timeout" in response.json()["detail"].lower()


async def test_chat_llm_upstream_error_returns_502(client, monkeypatch):
    async def fake_upstream(client, messages):
        raise LLMUpstreamError("LLM API returned status 500")

    monkeypatch.setattr(chat_service, "call_llm", fake_upstream)

    response = await client.post("/chat", json={"message": "你好"})

    assert response.status_code == 502
    assert "status 500" in response.json()["detail"]


async def test_chat_llm_configuration_error_returns_500(client, monkeypatch):
    async def fake_config(client, messages):
        raise LLMConfigurationError("缺少 LLM 配置环境变量: DEEPSEEK_API_KEY")

    monkeypatch.setattr(chat_service, "call_llm", fake_config)

    response = await client.post("/chat", json={"message": "你好"})

    assert response.status_code == 500
    assert "DEEPSEEK_API_KEY" in response.json()["detail"]


async def test_chat_success_returns_reply_and_persists_messages(client, monkeypatch):
    async def fake_call_llm(client, messages):
        assert messages[-1] == {"role": "user", "content": "你好"}
        return "你好，我是模拟模型。"

    monkeypatch.setattr(chat_service, "call_llm", fake_call_llm)

    response = await client.post("/chat", json={"message": "你好"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "你好，我是模拟模型。"
    assert body["conversation_id"] > 0

    # 接口成功后，会话与两条消息都应真实落库
    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, body["conversation_id"])
        assert conversation is not None
        assert conversation.title == "你好"

        messages = await _messages_of(session, body["conversation_id"])
        assert [(m.role, m.content) for m in messages] == [
            ("user", "你好"),
            ("assistant", "你好，我是模拟模型。"),
        ]


async def test_chat_stream_success_returns_full_response(client, monkeypatch):
    async def fake_stream(client, messages):
        yield "你好"
        yield "，世界。"

    monkeypatch.setattr(chat_service, "stream_llm", fake_stream)

    response = await client.post("/chat/stream", json={"message": "你好"})

    assert response.status_code == 200
    assert response.headers["X-Conversation-Id"]
    assert response.text == "你好，世界。"

    # 流式结束后，完整回复也应已落库
    conversation_id = int(response.headers["X-Conversation-Id"])

    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None

        messages = await _messages_of(session, conversation_id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", "你好"),
            ("assistant", "你好，世界。"),
        ]
