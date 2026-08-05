import httpx
import pytest
from sqlalchemy import func, select

from app.core.exceptions import ConversationNotFoundError, LLMTimeoutError
from app.db.session import AsyncSessionFactory
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.services import chat_service


# 40 个汉字，用于验证标题只取前 30 字
LONG_MESSAGE = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一二三四五"


async def _count_rows(session, model: type) -> int:
    result = await session.execute(
        select(func.count()).select_from(model)
    )
    return result.scalar_one()


async def _messages_of(session, conversation_id: int) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list(result.scalars().all())


async def test_chat_creates_conversation_and_saves_messages(
    fresh_schema, monkeypatch,
):
    async def fake_call_llm(client, messages):
        assert messages[-1] == {"role": "user", "content": "你好"}
        return "你好，我是模拟模型。"

    monkeypatch.setattr(chat_service, "call_llm", fake_call_llm)

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            result = await chat_service.chat(
                client=client,
                session=session,
                conversation_id=None,
                message="你好",
            )

    assert result.reply == "你好，我是模拟模型。"

    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, result.conversation_id)
        assert conversation is not None
        assert conversation.title == "你好"

        messages = await _messages_of(session, result.conversation_id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", "你好"),
            ("assistant", "你好，我是模拟模型。"),
        ]


async def test_chat_title_uses_first_30_chars(fresh_schema, monkeypatch):
    async def fake_call_llm(client, messages):
        return "回复"

    monkeypatch.setattr(chat_service, "call_llm", fake_call_llm)

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            result = await chat_service.chat(
                client=client,
                session=session,
                conversation_id=None,
                message=LONG_MESSAGE,
            )

    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, result.conversation_id)
        assert conversation is not None
        assert conversation.title == LONG_MESSAGE[:30]


async def test_chat_uses_existing_conversation(fresh_schema, monkeypatch):
    async def fake_call_llm(client, messages):
        return "回复"

    monkeypatch.setattr(chat_service, "call_llm", fake_call_llm)

    async with AsyncSessionFactory() as session:
        conversation = await ConversationRepository(session).create("已有会话")
        await session.commit()
        conversation_id = conversation.id

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            result = await chat_service.chat(
                client=client,
                session=session,
                conversation_id=conversation_id,
                message="继续聊",
            )

    assert result.conversation_id == conversation_id

    async with AsyncSessionFactory() as session:
        assert await _count_rows(session, Conversation) == 1
        messages = await _messages_of(session, conversation_id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", "继续聊"),
            ("assistant", "回复"),
        ]


async def test_chat_nonexistent_conversation_raises_not_found(
    fresh_schema, monkeypatch,
):
    async def fake_call_llm(client, messages):
        raise AssertionError("不存在的会话不应该走到 LLM 调用")

    monkeypatch.setattr(chat_service, "call_llm", fake_call_llm)

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            with pytest.raises(ConversationNotFoundError):
                await chat_service.chat(
                    client=client,
                    session=session,
                    conversation_id=999999,
                    message="你好",
                )


async def test_chat_llm_timeout_keeps_user_message_committed(
    fresh_schema, monkeypatch,
):
    async def fake_timeout(client, messages):
        raise LLMTimeoutError("LLM request timeout")

    monkeypatch.setattr(chat_service, "call_llm", fake_timeout)

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            with pytest.raises(LLMTimeoutError):
                await chat_service.chat(
                    client=client,
                    session=session,
                    conversation_id=None,
                    message="你好",
                )

    # 短事务 1 必须已提交：会话和 user 消息在，assistant 消息不落库
    async with AsyncSessionFactory() as session:
        conversation = (await session.execute(select(Conversation))).scalars().first()
        assert conversation is not None
        messages = await _messages_of(session, conversation.id)
        assert [(m.role, m.content) for m in messages] == [("user", "你好")]


async def test_chat_stream_saves_full_reply(fresh_schema, monkeypatch):
    async def fake_stream(client, messages):
        yield "你好"
        yield "，世界。"

    monkeypatch.setattr(chat_service, "stream_llm", fake_stream)

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            conversation_id, chunks = await chat_service.chat_stream(
                client=client,
                session=session,
                conversation_id=None,
                message="你好",
            )

            reply_parts = [chunk async for chunk in chunks]

    assert "".join(reply_parts) == "你好，世界。"

    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        messages = await _messages_of(session, conversation_id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", "你好"),
            ("assistant", "你好，世界。"),
        ]
