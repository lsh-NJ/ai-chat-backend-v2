"""Worker logic for retrying assistant message persistence."""

import logging
from collections.abc import Mapping
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.types import RedisDecodedFields, RedisFields
from app.db.session import AsyncSessionFactory
from app.queue.message_retry_queue import (
    CONSUMER_GROUP_NAME,
    DEAD_LETTER_STREAM_KEY,
    DEAD_LETTER_STREAM_MAX_LENGTH,
    RETRY_STREAM_KEY,
    deserialize_retry_job,
    serialize_retry_job,
)
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.retry_job import MessageRetryJob


logger = logging.getLogger("app")

MAX_DELIVERY_ATTEMPTS = 3
RECLAIM_IDLE_TIME_MS = 60_000
CLAIM_BATCH_SIZE = 10
RetryOutcome = Literal["acked", "dead_lettered"]


class RetryJobRejected(Exception):
    """任务契约或资源归属校验失败，等待后续死信处理。"""


async def process_retry_entry(
    redis: Redis,
    entry_id: str,
    fields: Mapping[str, str],
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionFactory,
    delivery_count: int = 1,
) -> RetryOutcome:
    """处理一条 Stream entry。

    ACK 被刻意放在 session context 之后：只有 PostgreSQL commit 成功，
    任务才会从 Consumer Group 的 pending 集合中移除。数据库或 Redis
    发生异常时直接向上抛出，由上层 Worker 保留 pending 状态并记录脱敏信息。
    """
    if delivery_count < 1:
        raise ValueError("delivery_count must be at least 1")

    try:
        job: MessageRetryJob = deserialize_retry_job(dict(fields))
    except (ValueError, TypeError):
        # 不把 Pydantic 的原始错误交给日志层，避免异常详情意外包含正文。
        await _move_to_dead_letter(
            redis=redis,
            entry_id=entry_id,
            job=None,
            delivery_count=delivery_count,
            reason="payload_validation",
        )
        _log_worker_event(
            "retry task dead-lettered",
            attempt=delivery_count,
            error_type="payload_validation",
        )
        return "dead_lettered"

    if delivery_count > MAX_DELIVERY_ATTEMPTS:
        await _move_to_dead_letter(
            redis=redis,
            entry_id=entry_id,
            job=job,
            delivery_count=delivery_count,
            reason="max_attempts",
        )
        _log_worker_event(
            "retry task dead-lettered",
            job=job,
            attempt=delivery_count,
            error_type="max_attempts",
        )
        return "dead_lettered"

    try:
        async with session_factory() as session:
            try:
                conversation = await ConversationRepository(session).get_by_id_for_user(
                    conversation_id=job.conversation_id,
                    user_id=job.user_id,
                )
                if conversation is None:
                    raise RetryJobRejected(
                        "retry job conversation ownership validation failed"
                    )

                await MessageRepository(session).add_idempotent(
                    conversation_id=job.conversation_id,
                    role="assistant",
                    content=job.content,
                    is_complete=job.is_complete,
                    idempotency_key=job.idempotency_key,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except RetryJobRejected:
        await _move_to_dead_letter(
            redis=redis,
            entry_id=entry_id,
            job=job,
            delivery_count=delivery_count,
            reason="conversation_ownership",
        )
        _log_worker_event(
            "retry task dead-lettered",
            job=job,
            attempt=delivery_count,
            error_type="conversation_ownership",
        )
        return "dead_lettered"
    except SQLAlchemyError as exc:
        _log_worker_event(
            "retry task failed",
            job=job,
            attempt=delivery_count,
            error_type=type(exc).__name__,
        )
        if delivery_count >= MAX_DELIVERY_ATTEMPTS:
            await _move_to_dead_letter(
                redis=redis,
                entry_id=entry_id,
                job=job,
                delivery_count=delivery_count,
                reason="max_attempts",
            )
            _log_worker_event(
                "retry task dead-lettered",
                job=job,
                attempt=delivery_count,
                error_type="max_attempts",
            )
            return "dead_lettered"
        raise

    # 绝不能把 ACK 放到 commit 之前，否则数据库失败会造成永久丢任务。
    try:
        await redis.xack(
            RETRY_STREAM_KEY,
            CONSUMER_GROUP_NAME,
            entry_id,
        )
    except Exception as exc:
        _log_worker_event(
            "retry task ack failed",
            job=job,
            attempt=delivery_count,
            error_type=type(exc).__name__,
        )
        raise

    _log_worker_event(
        "retry task acknowledged",
        job=job,
        attempt=delivery_count,
    )
    return "acked"


async def reclaim_pending_entries(
    redis: Redis,
    consumer_name: str,
    min_idle_time_ms: int = RECLAIM_IDLE_TIME_MS,
    start_id: str = "0-0",
    count: int = CLAIM_BATCH_SIZE,
) -> tuple[str, list[tuple[str, RedisDecodedFields, int]], list[str]]:
    """认领空闲 pending entry，并返回投递次数和已删除 entry ID。

    XAUTOCLAIM 的返回值不包含 ``times_delivered``，所以认领后用
    XPENDING RANGE 查询每条 entry 的投递次数，交给处理函数决定是否死信。
    Redis 可能同时返回已从 Stream 裁剪、但仍在 PEL 中的 entry ID；这些 ID
    无法恢复正文，必须交给调用方记录指标或告警，不能静默丢弃。
    """
    if min_idle_time_ms < 0:
        raise ValueError("min_idle_time_ms must be nonnegative")
    if count < 1:
        raise ValueError("count must be at least 1")

    result = await redis.xautoclaim(
        name=RETRY_STREAM_KEY,
        groupname=CONSUMER_GROUP_NAME,
        consumername=consumer_name,
        min_idle_time=min_idle_time_ms,
        start_id=start_id,
        count=count,
    )
    next_start_id = str(result[0])
    claimed_entries = result[1]
    deleted_entry_ids = (
        [str(raw_entry_id) for raw_entry_id in result[2]]
        if len(result) > 2
        else []
    )
    entries: list[tuple[str, RedisDecodedFields, int]] = []

    for raw_entry_id, raw_fields in claimed_entries:
        entry_id = str(raw_entry_id)
        pending_rows = await redis.xpending_range(
            name=RETRY_STREAM_KEY,
            groupname=CONSUMER_GROUP_NAME,
            min=entry_id,
            max=entry_id,
            count=1,
        )
        if not pending_rows:
            # Stream trim 可能在认领前后删除 entry；没有 payload 就不能重试。
            continue
        delivery_count = int(pending_rows[0]["times_delivered"])
        entries.append((entry_id, dict(raw_fields), delivery_count))

    return next_start_id, entries, deleted_entry_ids


async def _move_to_dead_letter(
    redis: Redis,
    entry_id: str,
    job: MessageRetryJob | None,
    delivery_count: int,
    reason: str,
) -> None:
    """在同一个 Redis transaction 中写 DLQ 并 ACK 原任务。"""
    dlq_fields: RedisFields = {
        "source_entry_id": entry_id,
        "attempt": str(delivery_count),
        "reason": reason,
    }
    if job is not None:
        dead_letter_job = job.model_copy(
            update={"attempt": max(job.attempt, delivery_count)}
        )
        dlq_fields.update(serialize_retry_job(dead_letter_job))
    else:
        dlq_fields["error_type"] = reason

    async with redis.pipeline(transaction=True) as pipeline:
        pipeline.xadd(
            name=DEAD_LETTER_STREAM_KEY,
            fields=dlq_fields,
            maxlen=DEAD_LETTER_STREAM_MAX_LENGTH,
            approximate=False,
        )
        pipeline.xack(
            RETRY_STREAM_KEY,
            CONSUMER_GROUP_NAME,
            entry_id,
        )
        await pipeline.execute()


def _log_worker_event(
    message: str,
    *,
    attempt: int,
    job: MessageRetryJob | None = None,
    error_type: str | None = None,
) -> None:
    """记录可关联但脱敏的 Worker 事件。"""
    extra: dict[str, object] = {
        "attempt": attempt,
        "status": message,
    }
    if job is not None:
        extra.update(
            {
                "job_id": str(job.job_id),
                "conversation_id": job.conversation_id,
            }
        )
    if error_type is not None:
        extra["error_type"] = error_type
    logger.info(message, extra=extra)
