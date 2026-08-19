"""Redis Stream producer and Consumer Group setup for message retry jobs."""

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.core.types import RedisDecodedFields, RedisFields
from app.schemas.retry_job import MessageRetryJob

RETRY_STREAM_KEY = "message-retry:v1"
DEAD_LETTER_STREAM_KEY = "message-retry:dead-letter:v1"
CONSUMER_GROUP_NAME = "message-retry-workers:v1"
STREAM_MAX_LENGTH = 10_000
DEAD_LETTER_STREAM_MAX_LENGTH = 10_000


def serialize_retry_job(job: MessageRetryJob) -> RedisFields:
    """把 Pydantic 对象转换成 Redis XADD 能接受的字段"""
    return {"payload": job.model_dump_json()}


def deserialize_retry_job(fields: RedisDecodedFields) -> MessageRetryJob:
    """把 Redis 读取出来的字段恢复成 MessageRetryJob"""
    payload = fields.get("payload")
    if payload is None:
        raise ValueError("retry job payload is missing")
    return MessageRetryJob.model_validate_json(payload)


async def ensure_consumer_group(redis: Redis) -> None:
    """确保 Consumer Group 已经创建，如果没有创建就进行创建"""
    try:
        await redis.xgroup_create(
            name=RETRY_STREAM_KEY,
            groupname=CONSUMER_GROUP_NAME,
            id="0-0",
            mkstream=True,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def enqueue_retry_job(redis: Redis, job: MessageRetryJob) -> str:
    """将一个重试任务写入 stream 并返回 entry ID"""
    entry_id = await redis.xadd(
        name=RETRY_STREAM_KEY,
        fields=serialize_retry_job(job),
        maxlen=STREAM_MAX_LENGTH,
        approximate=False,
    )
    return str(entry_id)
