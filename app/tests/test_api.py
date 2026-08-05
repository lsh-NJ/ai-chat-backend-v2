import httpx
import pytest

from app.core.exceptions import (
    LLMConfigurationError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.db.session import AsyncSessionFactory, get_db
from app.main import app
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


async def test_chat_stream_returns_conversation_id_header(client, monkeypatch):
    async def fake_stream(client, messages):
        yield "你好"
        yield "，世界。"

    monkeypatch.setattr(chat_service, "stream_llm", fake_stream)

    response = await client.post("/chat/stream", json={"message": "你好"})

    assert response.status_code == 200
    assert response.headers["X-Conversation-Id"]
    assert response.text == "你好，世界。"
