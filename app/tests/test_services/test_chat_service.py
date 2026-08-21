import logging
from uuid import uuid4

import pytest
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.exceptions import (
    ConversationNotFoundError,
    LLMStreamError,
    LLMTimeoutError,
)
from app.db.session import AsyncSessionFactory
from app.llm.contracts import LLMRole
from app.models.conversation import Conversation
from app.models.message import Message
from app.queue import message_retry_queue as retry_queue
from app.repositories.conversation_repository import ConversationRepository
from app.services import chat_service
from app.workers.message_retry_worker import process_retry_entry

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
    fresh_schema, test_user_id, redis_client, llm_provider,
):
    async def fake_complete(messages):
        assert messages[0].role == LLMRole.SYSTEM
        assert messages[-1].role == LLMRole.USER
        assert messages[-1].content == "你好"
        return "你好，我是模拟模型。"

    llm_provider.complete_handler = fake_complete

    async with AsyncSessionFactory() as session:
        result = await chat_service.chat(
            provider=llm_provider,
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
    llm_provider,
):
    async def fake_complete(messages):
        return "回复"

    llm_provider.complete_handler = fake_complete

    async with AsyncSessionFactory() as session:
        result = await chat_service.chat(
            provider=llm_provider,
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
    llm_provider,
):
    async def fake_complete(messages):
        return "回复"

    llm_provider.complete_handler = fake_complete

    async with AsyncSessionFactory() as session:
        conversation = await ConversationRepository(session).create(
            "已有会话",
            test_user_id,
        )
        await session.commit()
        conversation_id = conversation.id

    async with AsyncSessionFactory() as session:
        result = await chat_service.chat(
            provider=llm_provider,
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
    fresh_schema, test_user_id, redis_client, llm_provider,
):
    async with AsyncSessionFactory() as session:
        with pytest.raises(ConversationNotFoundError):
            await chat_service.chat(
                provider=llm_provider,
                session=session,
                conversation_id=999999,
                message="你好",
                user_id=test_user_id,
                redis=redis_client,
            )

    assert llm_provider.complete_calls == []


# 目标：LLM 超时抛错，但短事务 1 已提交（会话 + user 消息保留，assistant 不落库）
async def test_chat_llm_timeout_keeps_user_message_committed(
    fresh_schema, test_user_id, redis_client, llm_provider,
):
    async def fake_timeout(messages):
        raise LLMTimeoutError("LLM request timeout")

    llm_provider.complete_handler = fake_timeout

    async with AsyncSessionFactory() as session:
        with pytest.raises(LLMTimeoutError):
            await chat_service.chat(
                provider=llm_provider,
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
    llm_provider,
):
    async def fake_stream(messages):
        yield "你好"
        yield "，世界。"

    llm_provider.stream_handler = fake_stream

    async with AsyncSessionFactory() as session:
        conversation_id, chunks = await chat_service.chat_stream(
            provider=llm_provider,
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
        assert messages[-1].idempotency_key is not None
    redis_client.xadd.assert_not_awaited()


async def test_committed_assistant_save_exception_is_idempotent_under_worker(
    fresh_schema,
    test_user_id,
    redis_test_client,
    llm_provider,
    monkeypatch,
):
    async def fake_stream(messages):
        yield "数据库已提交但调用结果不确定"

    original_save_message = chat_service.save_message

    async def commit_then_raise(*args, **kwargs):
        result = await original_save_message(*args, **kwargs)
        if kwargs["role"] == "assistant":
            raise SQLAlchemyError("commit result unknown")
        return result

    await retry_queue.ensure_consumer_group(redis_test_client)
    llm_provider.stream_handler = fake_stream
    monkeypatch.setattr(chat_service, "save_message", commit_then_raise)

    async with AsyncSessionFactory() as session:
        conversation_id, chunks = await chat_service.chat_stream(
            provider=llm_provider,
            session=session,
            conversation_id=None,
            message="你好",
            user_id=test_user_id,
            redis=redis_test_client,
        )
        assert "".join([chunk async for chunk in chunks]) == (
            "数据库已提交但调用结果不确定"
        )

    async with AsyncSessionFactory() as session:
        messages = await _messages_of(session, conversation_id)
        assert [(message.role, message.content) for message in messages] == [
            ("user", "你好"),
            ("assistant", "数据库已提交但调用结果不确定"),
        ]
        stored_assistant = messages[-1]
        assert stored_assistant.idempotency_key is not None

    received = await redis_test_client.xreadgroup(
        groupname=retry_queue.CONSUMER_GROUP_NAME,
        consumername="uncertain-result-worker",
        streams={retry_queue.RETRY_STREAM_KEY: ">"},
        count=1,
        block=100,
    )
    assert len(received) == 1
    entry_id, fields = received[0][1][0]
    retry_job = retry_queue.deserialize_retry_job(fields)
    assert retry_job.idempotency_key == stored_assistant.idempotency_key

    assert await process_retry_entry(redis_test_client, entry_id, fields) == "acked"

    async with AsyncSessionFactory() as session:
        messages = await _messages_of(session, conversation_id)
        assert len(messages) == 2
        assert messages[-1].id == stored_assistant.id


# 目标：上游流自然结束时，assistant 消息标记为完整
async def test_chat_stream_marks_normal_reply_complete(
    fresh_schema,
    test_user_id,
    redis_client,
    llm_provider,
):
    async def fake_stream(messages):
        yield "完整"
        yield "回复"

    llm_provider.stream_handler = fake_stream

    async with AsyncSessionFactory() as session:
        conversation_id, chunks = await chat_service.chat_stream(
            provider=llm_provider,
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
    llm_provider,
):
    async def fake_stream(messages):
        yield "已经输出的部分"
        raise LLMStreamError("模拟上游中断")

    llm_provider.stream_handler = fake_stream

    async with AsyncSessionFactory() as session:
        conversation_id, chunks = await chat_service.chat_stream(
            provider=llm_provider,
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
    llm_provider,
):
    async def fake_stream(messages):
        yield "第一块"
        yield "不应被消费的第二块"

    llm_provider.stream_handler = fake_stream

    async with AsyncSessionFactory() as session:
        conversation_id, chunks = await chat_service.chat_stream(
            provider=llm_provider,
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
    llm_provider,
    monkeypatch,
    caplog,
):
    sensitive_reply = "绝不能写入日志的完整回复"

    async def fake_stream(messages):
        yield sensitive_reply

    original_save_message = chat_service.save_message
    attempted_idempotency_key = None

    async def fail_only_assistant_save(*args, **kwargs):
        nonlocal attempted_idempotency_key
        if kwargs["role"] == "assistant":
            attempted_idempotency_key = kwargs["idempotency_key"]
            raise SQLAlchemyError("绝不能写入日志的数据库异常详情")
        return await original_save_message(*args, **kwargs)

    llm_provider.stream_handler = fake_stream
    monkeypatch.setattr(chat_service, "save_message", fail_only_assistant_save)
    # Alembic 的 fileConfig 会禁用配置中未声明的既有 logger；测试中显式恢复，
    # 才能用 caplog 验证应用错误日志。
    monkeypatch.setattr(chat_service.logger, "disabled", False)
    monkeypatch.setattr(chat_service.logger, "propagate", True)
    caplog.set_level(logging.WARNING, logger="app")

    async with AsyncSessionFactory() as session:
        conversation_id, chunks = await chat_service.chat_stream(
            provider=llm_provider,
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
    assert error_record.status == "retry_enqueued"
    assert error_record.error_type == "SQLAlchemyError"
    assert error_record.exc_info is None

    assert redis_client.xadd.await_count == 1
    retry_fields = redis_client.xadd.await_args.kwargs["fields"]
    retry_job = chat_service.MessageRetryJob.model_validate_json(
        retry_fields["payload"]
    )
    assert retry_job.conversation_id == conversation_id
    assert retry_job.user_id == test_user_id
    assert retry_job.content == sensitive_reply
    assert retry_job.idempotency_key == attempted_idempotency_key

    async with AsyncSessionFactory() as session:
        messages = await _messages_of(session, conversation_id)
        assert [(message.role, message.content) for message in messages] == [
            ("user", "你好"),
        ]


async def test_chat_stream_queue_failure_does_not_break_response(
    fresh_schema,
    test_user_id,
    redis_client,
    llm_provider,
    monkeypatch,
    caplog,
):
    sensitive_reply = "队列失败时也不能写入日志的回复"

    async def fake_stream(messages):
        yield sensitive_reply

    original_save_message = chat_service.save_message

    async def fail_only_assistant_save(*args, **kwargs):
        if kwargs["role"] == "assistant":
            raise SQLAlchemyError("不应写入日志的数据库异常")
        return await original_save_message(*args, **kwargs)

    async def fail_enqueue(redis, job):
        raise RedisError("不应写入日志的 Redis 异常")

    llm_provider.stream_handler = fake_stream
    monkeypatch.setattr(chat_service, "save_message", fail_only_assistant_save)
    monkeypatch.setattr(chat_service, "enqueue_retry_job", fail_enqueue)
    monkeypatch.setattr(chat_service.logger, "disabled", False)
    monkeypatch.setattr(chat_service.logger, "propagate", True)
    caplog.set_level(logging.ERROR, logger="app")

    async with AsyncSessionFactory() as session:
        conversation_id, chunks = await chat_service.chat_stream(
            provider=llm_provider,
            session=session,
            conversation_id=None,
            message="你好",
            user_id=test_user_id,
            redis=redis_client,
        )
        reply = "".join([chunk async for chunk in chunks])

    assert reply == sensitive_reply
    error_record = next(
        record
        for record in caplog.records
        if "重试任务入队也失败" in record.getMessage()
    )
    assert error_record.status == "retry_enqueue_failed"
    assert error_record.error_type == "RedisError"
    assert sensitive_reply not in caplog.text
    assert "不应写入日志的数据库异常" not in caplog.text
    assert "不应写入日志的 Redis 异常" not in caplog.text

    async with AsyncSessionFactory() as session:
        messages = await _messages_of(session, conversation_id)
        assert [(message.role, message.content) for message in messages] == [
            ("user", "你好"),
        ]


async def test_worker_recovers_assistant_after_initial_save_failure(
    fresh_schema,
    test_user_id,
    redis_test_client,
    llm_provider,
    monkeypatch,
):
    reply = "由 Worker 补偿保存的回复"

    async def fake_stream(messages):
        yield reply

    original_save_message = chat_service.save_message

    async def fail_initial_assistant_save(*args, **kwargs):
        if kwargs["role"] == "assistant":
            raise SQLAlchemyError("temporary database failure")
        return await original_save_message(*args, **kwargs)

    await retry_queue.ensure_consumer_group(redis_test_client)
    llm_provider.stream_handler = fake_stream
    monkeypatch.setattr(
        chat_service,
        "save_message",
        fail_initial_assistant_save,
    )

    async with AsyncSessionFactory() as session:
        conversation_id, chunks = await chat_service.chat_stream(
            provider=llm_provider,
            session=session,
            conversation_id=None,
            message="你好",
            user_id=test_user_id,
            redis=redis_test_client,
        )
        assert "".join([chunk async for chunk in chunks]) == reply

    async with AsyncSessionFactory() as session:
        messages_before_retry = await _messages_of(session, conversation_id)
    assert [(message.role, message.content) for message in messages_before_retry] == [
        ("user", "你好"),
    ]

    received = await redis_test_client.xreadgroup(
        groupname=retry_queue.CONSUMER_GROUP_NAME,
        consumername="recovery-worker",
        streams={retry_queue.RETRY_STREAM_KEY: ">"},
        count=1,
        block=100,
    )
    assert len(received) == 1
    entry_id, fields = received[0][1][0]
    retry_job = retry_queue.deserialize_retry_job(fields)

    assert await process_retry_entry(redis_test_client, entry_id, fields) == "acked"

    async with AsyncSessionFactory() as session:
        messages_after_retry = await _messages_of(session, conversation_id)
    assert [(message.role, message.content) for message in messages_after_retry] == [
        ("user", "你好"),
        ("assistant", reply),
    ]
    assert messages_after_retry[-1].idempotency_key == retry_job.idempotency_key


async def test_save_type_error_propagates_without_enqueue(
    redis_client,
    monkeypatch,
):
    async def fail_save(*args, **kwargs):
        raise TypeError("save implementation bug")

    monkeypatch.setattr(chat_service, "save_message", fail_save)

    with pytest.raises(TypeError, match="save implementation bug"):
        await chat_service.persist_or_enqueue_assistant(
            session=None,
            redis=redis_client,
            conversation_id=1,
            user_id=1,
            content="不会进入日志的内容",
            is_complete=True,
            job_id=uuid4(),
            idempotency_key=uuid4(),
        )

    redis_client.xadd.assert_not_awaited()


async def test_enqueue_type_error_propagates(
    redis_client,
    monkeypatch,
):
    async def fail_save(*args, **kwargs):
        raise SQLAlchemyError("database unavailable")

    enqueue_calls = 0

    async def fail_enqueue(redis, job):
        nonlocal enqueue_calls
        enqueue_calls += 1
        raise TypeError("enqueue implementation bug")

    monkeypatch.setattr(chat_service, "save_message", fail_save)
    monkeypatch.setattr(chat_service, "enqueue_retry_job", fail_enqueue)

    with pytest.raises(TypeError, match="enqueue implementation bug"):
        await chat_service.persist_or_enqueue_assistant(
            session=None,
            redis=redis_client,
            conversation_id=1,
            user_id=1,
            content="不会进入日志的内容",
            is_complete=True,
            job_id=uuid4(),
            idempotency_key=uuid4(),
        )

    assert enqueue_calls == 1
    redis_client.xadd.assert_not_awaited()


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
