import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

import app.queue.message_retry_queue as retry_queue
from app.schemas.retry_job import MessageRetryJob


@pytest.fixture
async def clean_redis(real_redis):
    await real_redis.flushdb()
    yield real_redis
    await real_redis.flushdb()


def _job(**overrides: object) -> MessageRetryJob:
    payload: dict[str, object] = {
        "job_id": uuid4(),
        "idempotency_key": uuid4(),
        "conversation_id": 7,
        "user_id": 11,
        "content": "需要重试的 assistant 消息",
        "is_complete": True,
        "attempt": 0,
    }
    payload.update(overrides)
    return MessageRetryJob.model_validate(payload)


async def test_ensure_consumer_group_is_idempotent(clean_redis) -> None:
    await retry_queue.ensure_consumer_group(clean_redis)
    await retry_queue.ensure_consumer_group(clean_redis)

    groups = await clean_redis.xinfo_groups(retry_queue.RETRY_STREAM_KEY)

    assert len(groups) == 1
    assert groups[0]["name"] == retry_queue.CONSUMER_GROUP_NAME


async def test_enqueue_and_read_retry_job_from_consumer_group(clean_redis) -> None:
    await retry_queue.ensure_consumer_group(clean_redis)
    job = _job()

    entry_id = await retry_queue.enqueue_retry_job(clean_redis, job)
    messages = await clean_redis.xreadgroup(
        groupname=retry_queue.CONSUMER_GROUP_NAME,
        consumername="test-consumer",
        streams={retry_queue.RETRY_STREAM_KEY: ">"},
        count=1,
        block=100,
    )

    assert entry_id
    assert len(messages) == 1
    _, entries = messages[0]
    read_entry_id, fields = entries[0]
    rebuilt = retry_queue.deserialize_retry_job(fields)

    assert read_entry_id == entry_id
    assert rebuilt == job


def test_deserialize_rejects_missing_payload() -> None:
    with pytest.raises(ValueError, match="payload is missing"):
        retry_queue.deserialize_retry_job({})


def test_deserialize_rejects_malformed_json() -> None:
    with pytest.raises(ValidationError):
        retry_queue.deserialize_retry_job({"payload": "不是 JSON"})


def test_deserialize_rejects_invalid_schema_fields() -> None:
    with pytest.raises(ValidationError):
        retry_queue.deserialize_retry_job({"payload": '{"version": 2}'})

    payload = json.loads(_job().model_dump_json())
    payload["unexpected"] = "不允许的字段"
    with pytest.raises(ValidationError):
        retry_queue.deserialize_retry_job({"payload": json.dumps(payload)})


async def test_enqueue_preserves_user_and_conversation_scope(clean_redis) -> None:
    job = _job(conversation_id=23, user_id=42)

    await retry_queue.enqueue_retry_job(clean_redis, job)
    stored = await clean_redis.xrange(retry_queue.RETRY_STREAM_KEY)
    _, fields = stored[0]
    rebuilt = retry_queue.deserialize_retry_job(fields)

    assert rebuilt.user_id == 42
    assert rebuilt.conversation_id == 23
    assert rebuilt.idempotency_key == job.idempotency_key


async def test_enqueue_applies_stream_max_length(clean_redis, monkeypatch) -> None:
    monkeypatch.setattr(retry_queue, "STREAM_MAX_LENGTH", 3)

    for index in range(5):
        await retry_queue.enqueue_retry_job(
            clean_redis,
            _job(content=f"消息 {index}"),
        )

    assert await clean_redis.xlen(retry_queue.RETRY_STREAM_KEY) == 3


async def test_real_redis_fixture_is_available(real_redis) -> None:
    """Redis 不可用时由 fixture 抛错，测试不能通过 skip 掩盖环境错误。"""
    assert await real_redis.ping() is True
