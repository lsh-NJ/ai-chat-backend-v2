import logging

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    ConversationNotFoundError,
    LLMStreamError,
    LLMTimeoutError,
)
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


# 目标：chat 无会话时自动创建会话，并保存 user + assistant 两条消息
async def test_chat_creates_conversation_and_saves_messages(
    fresh_schema, test_user_id, redis_client, monkeypatch,
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
                user_id=test_user_id,
                redis=redis_client,
            )

    assert result.reply == "你好，我是模拟模型。"

    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, result.conversation_id)
        assert conversation is not None
        assert conversation.title == "你好"
        assert conversation.user_id == test_user_id

        messages = await _messages_of(session, result.conversation_id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", "你好"),
            ("assistant", "你好，我是模拟模型。"),
        ]


# 目标：自动标题取用户消息前 30 个字
async def test_chat_title_uses_first_30_chars(
    fresh_schema,
    test_user_id,
    redis_client,
    monkeypatch,
):
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
                user_id=test_user_id,
                redis=redis_client,
            )

    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, result.conversation_id)
        assert conversation is not None
        assert conversation.title == LONG_MESSAGE[:30]


# 目标：传入已有会话时不新建，消息追加到该会话
async def test_chat_uses_existing_conversation(
    fresh_schema,
    test_user_id,
    redis_client,
    monkeypatch,
):
    async def fake_call_llm(client, messages):
        return "回复"

    monkeypatch.setattr(chat_service, "call_llm", fake_call_llm)

    async with AsyncSessionFactory() as session:
        conversation = await ConversationRepository(session).create(
            "已有会话",
            test_user_id,
        )
        await session.commit()
        conversation_id = conversation.id

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            result = await chat_service.chat(
                client=client,
                session=session,
                conversation_id=conversation_id,
                message="继续聊",
                user_id=test_user_id,
                redis=redis_client,
            )

    assert result.conversation_id == conversation_id

    async with AsyncSessionFactory() as session:
        assert await _count_rows(session, Conversation) == 1
        messages = await _messages_of(session, conversation_id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", "继续聊"),
            ("assistant", "回复"),
        ]


# 目标：不存在的会话抛 ConversationNotFoundError，且不调用 LLM
async def test_chat_nonexistent_conversation_raises_not_found(
    fresh_schema, test_user_id, redis_client, monkeypatch,
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
                    user_id=test_user_id,
                    redis=redis_client,
                )


# 目标：LLM 超时抛错，但短事务 1 已提交（会话 + user 消息保留，assistant 不落库）
async def test_chat_llm_timeout_keeps_user_message_committed(
    fresh_schema, test_user_id, redis_client, monkeypatch,
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
                    user_id=test_user_id,
                    redis=redis_client,
                )

    # 短事务 1 必须已提交：会话和 user 消息在，assistant 消息不落库
    async with AsyncSessionFactory() as session:
        conversation = (await session.execute(select(Conversation))).scalars().first()
        assert conversation is not None
        messages = await _messages_of(session, conversation.id)
        assert [(m.role, m.content) for m in messages] == [("user", "你好")]


# 目标：流式结束后完整回复落库
async def test_chat_stream_saves_full_reply(
    fresh_schema,
    test_user_id,
    redis_client,
    monkeypatch,
):
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
                user_id=test_user_id,
                redis=redis_client,
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


# 目标：上游流自然结束时，assistant 消息标记为完整
async def test_chat_stream_marks_normal_reply_complete(
    fresh_schema,
    test_user_id,
    redis_client,
    monkeypatch,
):
    async def fake_stream(client, messages):
        yield "完整"
        yield "回复"

    monkeypatch.setattr(chat_service, "stream_llm", fake_stream)

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            conversation_id, chunks = await chat_service.chat_stream(
                client=client,
                session=session,
                conversation_id=None,
                message="你好",
                user_id=test_user_id,
                redis=redis_client,
            )
            reply = "".join([chunk async for chunk in chunks])

    assert reply == "完整回复"

    async with AsyncSessionFactory() as session:
        messages = await _messages_of(session, conversation_id)
        assistant_message = messages[-1]
        assert assistant_message.role == "assistant"
        assert assistant_message.content == "完整回复"
        assert assistant_message.is_complete is True


# 目标：LLM 输出部分内容后失败，保留部分回复并标记为不完整
async def test_chat_stream_marks_llm_interruption_incomplete(
    fresh_schema,
    test_user_id,
    redis_client,
    monkeypatch,
):
    async def fake_stream(client, messages):
        yield "已经输出的部分"
        raise LLMStreamError("模拟上游中断")

    monkeypatch.setattr(chat_service, "stream_llm", fake_stream)

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            conversation_id, chunks = await chat_service.chat_stream(
                client=client,
                session=session,
                conversation_id=None,
                message="你好",
                user_id=test_user_id,
                redis=redis_client,
            )
            reply = "".join([chunk async for chunk in chunks])

    assert reply == "已经输出的部分"

    async with AsyncSessionFactory() as session:
        messages = await _messages_of(session, conversation_id)
        assistant_message = messages[-1]
        assert assistant_message.role == "assistant"
        assert assistant_message.content.startswith("已经输出的部分")
        assert "模拟上游中断" in assistant_message.content
        assert assistant_message.is_complete is False


# 目标：客户端只消费一个 chunk 就关闭流，部分回复仍落库且标记为不完整
async def test_chat_stream_marks_client_closed_reply_incomplete(
    fresh_schema,
    test_user_id,
    redis_client,
    monkeypatch,
):
    async def fake_stream(client, messages):
        yield "第一块"
        yield "不应被消费的第二块"

    monkeypatch.setattr(chat_service, "stream_llm", fake_stream)

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            conversation_id, chunks = await chat_service.chat_stream(
                client=client,
                session=session,
                conversation_id=None,
                message="你好",
                user_id=test_user_id,
                redis=redis_client,
            )
            first_chunk = await anext(chunks)
            await chunks.aclose()

    assert first_chunk == "第一块"

    async with AsyncSessionFactory() as session:
        messages = await _messages_of(session, conversation_id)
        assistant_message = messages[-1]
        assert assistant_message.role == "assistant"
        assert assistant_message.content == "第一块"
        assert assistant_message.is_complete is False


# 目标：最终 assistant 保存失败不破坏已经产生的流，并留下可排查日志
async def test_chat_stream_assistant_save_failure_does_not_break_response(
    fresh_schema,
    test_user_id,
    redis_client,
    monkeypatch,
    caplog,
):
    sensitive_reply = "绝不能写入日志的完整回复"

    async def fake_stream(client, messages):
        yield sensitive_reply

    original_save_message = chat_service.save_message

    async def fail_only_assistant_save(*args, **kwargs):
        if kwargs["role"] == "assistant":
            raise RuntimeError("绝不能写入日志的数据库异常详情")
        return await original_save_message(*args, **kwargs)

    monkeypatch.setattr(chat_service, "stream_llm", fake_stream)
    monkeypatch.setattr(chat_service, "save_message", fail_only_assistant_save)
    # Alembic 的 fileConfig 会禁用配置中未声明的既有 logger；测试中显式恢复，
    # 才能用 caplog 验证应用错误日志。
    monkeypatch.setattr(chat_service.logger, "disabled", False)
    monkeypatch.setattr(chat_service.logger, "propagate", True)
    caplog.set_level(logging.ERROR, logger="app")

    async with httpx.AsyncClient() as client:
        async with AsyncSessionFactory() as session:
            conversation_id, chunks = await chat_service.chat_stream(
                client=client,
                session=session,
                conversation_id=None,
                message="你好",
                user_id=test_user_id,
                redis=redis_client,
            )
            reply = "".join([chunk async for chunk in chunks])

    assert reply == sensitive_reply
    assert any(
        "保存流式 assistant 消息失败" in record.getMessage()
        for record in caplog.records
    )
    assert sensitive_reply not in caplog.text
    assert "绝不能写入日志的数据库异常详情" not in caplog.text

    error_record = next(
        record
        for record in caplog.records
        if "保存流式 assistant 消息失败" in record.getMessage()
    )
    assert error_record.error_type == "RuntimeError"
    assert error_record.content_length == len(sensitive_reply)
    assert error_record.exc_info is None

    async with AsyncSessionFactory() as session:
        messages = await _messages_of(session, conversation_id)
        assert [(message.role, message.content) for message in messages] == [
            ("user", "你好"),
        ]


# 目标：PostgreSQL 真正拒绝写入后，save_message 会 rollback，原 session 可继续使用
async def test_save_message_rolls_back_failed_postgres_transaction(
    fresh_schema,
    test_user_id,
):
    async with AsyncSessionFactory() as session:
        conversation = await ConversationRepository(session).create(
            "事务恢复测试",
            test_user_id,
        )
        await session.commit()

        with pytest.raises(IntegrityError):
            await chat_service.save_message(
                session=session,
                conversation_id=conversation.id,
                role="invalid-role",
                content="触发 PostgreSQL CHECK 约束",
                is_complete=True,
            )

        # 必须使用同一个 session 查询；若 save_message 没有 rollback，
        # 这里会抛 PendingRollbackError / InFailedSQLTransaction。
        message_count = (
            await session.execute(select(func.count()).select_from(Message))
        ).scalar_one()

        assert message_count == 0
