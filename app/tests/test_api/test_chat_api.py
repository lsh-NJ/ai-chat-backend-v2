from sqlalchemy import select

from app.api.chat import to_http_exception
from app.core.exceptions import (
    LLMConfigurationError,
    LLMInputTooLongError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.db.session import AsyncSessionFactory
from app.llm.contracts import LLMRole
from app.models.conversation import Conversation
from app.models.message import Message


def test_input_too_long_maps_to_request_error() -> None:
    error = to_http_exception(LLMInputTooLongError("输入过长"))

    assert error.status_code == 422
    assert error.detail == "输入过长"


async def _messages_of(session, conversation_id: int) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list(result.scalars().all())


# /chat 传入不存在的会话 → 404，且不应触发 LLM 调用
async def test_chat_nonexistent_conversation_returns_404(
    client,
    auth_headers,
    llm_provider,
):
    response = await client.post(
        "/chat",
        json={"conversation_id": 999999, "message": "你好"},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]
    assert llm_provider.complete_calls == []


# 历史消息接口查询不存在的会话 → 404
async def test_history_missing_conversation_returns_404(client, auth_headers):
    response = await client.get(
        "/conversations/999999/messages",
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


# LLM 超时 → 504
async def test_chat_llm_timeout_returns_504(client, auth_headers, llm_provider):
    async def fake_timeout(messages):
        raise LLMTimeoutError("LLM request timeout")

    llm_provider.complete_handler = fake_timeout

    response = await client.post(
        "/chat",
        json={"message": "你好"},
        headers=auth_headers,
    )

    assert response.status_code == 504
    assert "timeout" in response.json()["detail"].lower()


# LLM 上游错误 → 502
async def test_chat_llm_upstream_error_returns_502(
    client,
    auth_headers,
    llm_provider,
):
    async def fake_upstream(messages):
        raise LLMUpstreamError("LLM API returned status 500")

    llm_provider.complete_handler = fake_upstream

    response = await client.post(
        "/chat",
        json={"message": "你好"},
        headers=auth_headers,
    )

    assert response.status_code == 502
    assert "status 500" in response.json()["detail"]


# LLM 配置缺失 → 500，detail 中带出缺失的配置项
async def test_chat_llm_configuration_error_returns_500(
    client,
    auth_headers,
    llm_provider,
):
    async def fake_config(messages):
        raise LLMConfigurationError("缺少 LLM 配置环境变量: DEEPSEEK_API_KEY")

    llm_provider.complete_handler = fake_config

    response = await client.post(
        "/chat",
        json={"message": "你好"},
        headers=auth_headers,
    )

    assert response.status_code == 500
    assert "DEEPSEEK_API_KEY" in response.json()["detail"]


# 正常对话返回 reply + conversation_id，且会话与两条消息真实落库
async def test_chat_success_returns_reply_and_persists_messages(
    client,
    auth_headers,
    llm_provider,
):
    async def fake_complete(messages):
        assert messages[0].role == LLMRole.SYSTEM
        assert messages[-1].role == LLMRole.USER
        assert messages[-1].content == "你好"
        return "你好，我是模拟模型。"

    llm_provider.complete_handler = fake_complete

    response = await client.post(
        "/chat",
        json={"message": "你好"},
        headers=auth_headers,
    )

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


# 流式对话完整返回文本 + X-Conversation-Id 头，结束后完整回复落库
async def test_chat_stream_success_returns_full_response(
    client,
    auth_headers,
    llm_provider,
):
    async def fake_stream(messages):
        assert messages[0].role == LLMRole.SYSTEM
        assert messages[-1].role == LLMRole.USER
        yield "你好"
        yield "，世界。"

    llm_provider.stream_handler = fake_stream

    response = await client.post(
        "/chat/stream",
        json={"message": "你好"},
        headers=auth_headers,
    )

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
