import logging
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionFactory
from app.models.message import Message
from app.queue import message_retry_queue as retry_queue
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.retry_job import MessageRetryJob
from app.workers.message_retry_worker import (
    MAX_DELIVERY_ATTEMPTS,
    reclaim_pending_entries,
    process_retry_entry,
)


def _job(conversation_id: int, user_id: int, **overrides: object) -> MessageRetryJob:
    payload: dict[str, object] = {
        "job_id": uuid4(),
        "idempotency_key": uuid4(),
        "conversation_id": conversation_id,
        "user_id": user_id,
        "content": "需要补偿保存的 assistant 消息",
        "is_complete": True,
        "attempt": 0,
    }
    payload.update(overrides)
    return MessageRetryJob.model_validate(payload)


async def _create_conversation(user_id: int) -> int:
    async with AsyncSessionFactory() as session:
        conversation = await ConversationRepository(session).create(
            title="Worker 测试",
            user_id=user_id,
        )
        await session.commit()
        return conversation.id


async def _deliver_one(redis, job: MessageRetryJob) -> tuple[str, dict[str, str]]:
    await retry_queue.ensure_consumer_group(redis)
    entry_id = await retry_queue.enqueue_retry_job(redis, job)
    messages = await redis.xreadgroup(
        groupname=retry_queue.CONSUMER_GROUP_NAME,
        consumername="test-worker",
        streams={retry_queue.RETRY_STREAM_KEY: ">"},
        count=1,
        block=100,
    )
    assert len(messages) == 1
    _, entries = messages[0]
    read_entry_id, fields = entries[0]
    assert read_entry_id == entry_id
    return entry_id, fields


async def test_commit_success_is_followed_by_ack(
    fresh_schema,
    real_redis,
    test_user_id: int,
    monkeypatch,
) -> None:
    conversation_id = await _create_conversation(test_user_id)
    job = _job(conversation_id, test_user_id)
    entry_id, fields = await _deliver_one(real_redis, job)

    events: list[str] = []
    original_commit = AsyncSession.commit
    original_xack = real_redis.xack

    async def record_commit(self: AsyncSession) -> None:
        events.append("commit")
        await original_commit(self)

    async def record_xack(*args, **kwargs) -> int:
        events.append("ack")
        return await original_xack(*args, **kwargs)

    monkeypatch.setattr(AsyncSession, "commit", record_commit)
    monkeypatch.setattr(real_redis, "xack", record_xack)

    await process_retry_entry(real_redis, entry_id, fields)

    assert events == ["commit", "ack"]
    pending = await real_redis.xpending(
        retry_queue.RETRY_STREAM_KEY,
        retry_queue.CONSUMER_GROUP_NAME,
    )
    assert pending["pending"] == 0

    async with AsyncSessionFactory() as session:
        stored = await session.scalar(
            select(Message).where(Message.idempotency_key == job.idempotency_key)
        )

    assert stored is not None
    assert stored.role == "assistant"
    assert stored.content == job.content


async def test_commit_failure_leaves_entry_pending(
    fresh_schema,
    real_redis,
    test_user_id: int,
    monkeypatch,
) -> None:
    conversation_id = await _create_conversation(test_user_id)
    job = _job(conversation_id, test_user_id)
    entry_id, fields = await _deliver_one(real_redis, job)

    async def fail_commit(self: AsyncSession) -> None:
        raise SQLAlchemyError("database unavailable")

    ack_calls = 0

    async def record_xack(*args, **kwargs) -> int:
        nonlocal ack_calls
        ack_calls += 1
        return 0

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    monkeypatch.setattr(real_redis, "xack", record_xack)

    with pytest.raises(SQLAlchemyError, match="database unavailable"):
        await process_retry_entry(real_redis, entry_id, fields)

    assert ack_calls == 0
    pending = await real_redis.xpending(
        retry_queue.RETRY_STREAM_KEY,
        retry_queue.CONSUMER_GROUP_NAME,
    )
    assert pending["pending"] == 1

    async with AsyncSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(Message))
    assert count == 0


async def test_reprocessing_same_job_is_idempotent(
    fresh_schema,
    real_redis,
    test_user_id: int,
) -> None:
    conversation_id = await _create_conversation(test_user_id)
    job = _job(conversation_id, test_user_id)

    first_entry_id, first_fields = await _deliver_one(real_redis, job)
    await process_retry_entry(real_redis, first_entry_id, first_fields)

    second_entry_id, second_fields = await _deliver_one(real_redis, job)
    await process_retry_entry(real_redis, second_entry_id, second_fields)

    async with AsyncSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(Message))
        stored = await session.scalar(
            select(Message).where(Message.idempotency_key == job.idempotency_key)
        )
    assert count == 1
    assert stored is not None
    assert stored.role == "assistant"
    assert stored.content == job.content

    pending = await real_redis.xpending(
        retry_queue.RETRY_STREAM_KEY,
        retry_queue.CONSUMER_GROUP_NAME,
    )
    assert pending["pending"] == 0


async def test_wrong_user_cannot_write_to_conversation(
    fresh_schema,
    real_redis,
    test_user_id: int,
) -> None:
    conversation_id = await _create_conversation(test_user_id)
    job = _job(conversation_id, test_user_id + 1)
    entry_id, fields = await _deliver_one(real_redis, job)

    outcome = await process_retry_entry(real_redis, entry_id, fields)
    assert outcome == "dead_lettered"

    pending = await real_redis.xpending(
        retry_queue.RETRY_STREAM_KEY,
        retry_queue.CONSUMER_GROUP_NAME,
    )
    assert pending["pending"] == 0

    dead_letters = await real_redis.xrange(retry_queue.DEAD_LETTER_STREAM_KEY)
    assert len(dead_letters) == 1
    _, dead_letter_fields = dead_letters[0]
    assert dead_letter_fields["reason"] == "conversation_ownership"


async def test_reclaim_pending_entry_returns_delivery_count(
    fresh_schema,
    real_redis,
    test_user_id: int,
) -> None:
    conversation_id = await _create_conversation(test_user_id)
    job = _job(conversation_id, test_user_id)
    entry_id, _ = await _deliver_one(real_redis, job)

    next_start_id, claimed, deleted_entry_ids = await reclaim_pending_entries(
        real_redis,
        consumer_name="replacement-worker",
        min_idle_time_ms=0,
    )

    assert next_start_id
    assert deleted_entry_ids == []
    assert len(claimed) == 1
    claimed_entry_id, fields, delivery_count = claimed[0]
    assert claimed_entry_id == entry_id
    assert delivery_count == 2

    outcome = await process_retry_entry(
        real_redis,
        claimed_entry_id,
        fields,
        delivery_count=delivery_count,
    )
    assert outcome == "acked"

    pending = await real_redis.xpending(
        retry_queue.RETRY_STREAM_KEY,
        retry_queue.CONSUMER_GROUP_NAME,
    )
    assert pending["pending"] == 0


async def test_reclaim_returns_deleted_pending_entry_ids(
    real_redis,
    monkeypatch,
) -> None:
    async def fake_xautoclaim(*args, **kwargs):
        return ["0-0", [], ["171-0", "172-0"]]

    monkeypatch.setattr(real_redis, "xautoclaim", fake_xautoclaim)

    next_start_id, claimed, deleted_entry_ids = await reclaim_pending_entries(
        real_redis,
        consumer_name="replacement-worker",
        min_idle_time_ms=0,
    )

    assert next_start_id == "0-0"
    assert claimed == []
    assert deleted_entry_ids == ["171-0", "172-0"]


async def test_max_delivery_attempts_moves_job_to_dead_letter(
    fresh_schema,
    real_redis,
    test_user_id: int,
    monkeypatch,
    caplog,
) -> None:
    caplog.set_level(logging.INFO, logger="app")
    conversation_id = await _create_conversation(test_user_id)
    job = _job(conversation_id, test_user_id)
    entry_id, fields = await _deliver_one(real_redis, job)

    async def fail_commit(self: AsyncSession) -> None:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)

    with pytest.raises(SQLAlchemyError, match="database unavailable"):
        await process_retry_entry(
            real_redis,
            entry_id,
            fields,
            delivery_count=1,
        )

    _, claimed_on_second_delivery, deleted_entry_ids = await reclaim_pending_entries(
        real_redis,
        consumer_name="retry-worker-2",
        min_idle_time_ms=0,
    )
    assert deleted_entry_ids == []
    assert len(claimed_on_second_delivery) == 1
    second_entry_id, second_fields, second_delivery_count = claimed_on_second_delivery[0]
    assert second_entry_id == entry_id
    assert second_delivery_count == 2

    with pytest.raises(SQLAlchemyError, match="database unavailable"):
        await process_retry_entry(
            real_redis,
            second_entry_id,
            second_fields,
            delivery_count=second_delivery_count,
        )

    _, claimed_on_third_delivery, deleted_entry_ids = await reclaim_pending_entries(
        real_redis,
        consumer_name="retry-worker-3",
        min_idle_time_ms=0,
    )
    assert deleted_entry_ids == []
    assert len(claimed_on_third_delivery) == 1
    third_entry_id, third_fields, third_delivery_count = claimed_on_third_delivery[0]
    assert third_entry_id == entry_id
    assert third_delivery_count == MAX_DELIVERY_ATTEMPTS

    outcome = await process_retry_entry(
        real_redis,
        third_entry_id,
        third_fields,
        delivery_count=third_delivery_count,
    )
    assert outcome == "dead_lettered"

    pending = await real_redis.xpending(
        retry_queue.RETRY_STREAM_KEY,
        retry_queue.CONSUMER_GROUP_NAME,
    )
    assert pending["pending"] == 0

    dead_letters = await real_redis.xrange(retry_queue.DEAD_LETTER_STREAM_KEY)
    assert len(dead_letters) == 1
    _, dead_letter_fields = dead_letters[0]
    dead_letter_job = retry_queue.deserialize_retry_job(dead_letter_fields)
    assert dead_letter_job.idempotency_key == job.idempotency_key
    assert dead_letter_job.attempt == MAX_DELIVERY_ATTEMPTS
    assert dead_letter_fields["reason"] == "max_attempts"
    assert job.content not in caplog.text


async def test_invalid_payload_is_rejected_before_database_path(
    fresh_schema,
    real_redis,
) -> None:
    await retry_queue.ensure_consumer_group(real_redis)
    entry_id = await real_redis.xadd(
        retry_queue.RETRY_STREAM_KEY,
        {"payload": "not-json"},
    )
    messages = await real_redis.xreadgroup(
        groupname=retry_queue.CONSUMER_GROUP_NAME,
        consumername="test-worker",
        streams={retry_queue.RETRY_STREAM_KEY: ">"},
        count=1,
        block=100,
    )
    assert len(messages) == 1
    _, entries = messages[0]
    read_entry_id, fields = entries[0]
    assert read_entry_id == entry_id

    outcome = await process_retry_entry(
        real_redis,
        entry_id=entry_id,
        fields=fields,
    )

    assert outcome == "dead_lettered"
    pending = await real_redis.xpending(
        retry_queue.RETRY_STREAM_KEY,
        retry_queue.CONSUMER_GROUP_NAME,
    )
    assert pending["pending"] == 0

    dead_letters = await real_redis.xrange(retry_queue.DEAD_LETTER_STREAM_KEY)
    assert len(dead_letters) == 1
    _, dead_letter_fields = dead_letters[0]
    assert dead_letter_fields["source_entry_id"] == entry_id
    assert dead_letter_fields["reason"] == "payload_validation"
    assert dead_letter_fields["error_type"] == "payload_validation"
