import json
import logging

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConversationNotFoundError
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.conversation import ConversationOut

logger = logging.getLogger("app")
CONVERSATION_LIST_CACHE_TTL_SECONDS = 60


def _log_cache_failure(
    message: str,
    user_id: int,
    exc: Exception,
) -> None:
    logger.warning(
        message,
        extra={
            "user_id": user_id,
            "error_type": type(exc).__name__,
        },
    )


def _conversation_list_cache_key(user_id: int) -> str:
    return f"conversations:user:{user_id}:v1"


def _serialize_conversations(
    conversations: list[ConversationOut]
) -> str:
    data = [
        conversation.model_dump(mode="json")
        for conversation in conversations
    ]
    return json.dumps(data, ensure_ascii=False)


def _deserialize_conversations(
    cached: str
) -> list[ConversationOut]:
    data = json.loads(cached)

    return [
        ConversationOut.model_validate(item)
        for item in data
    ]


async def _invalidate_conversation_list_cache(
    redis: Redis,
    user_id: int,
) -> None:
    try:
        await redis.delete(_conversation_list_cache_key(user_id))
    except RedisError as exc:
        _log_cache_failure("会话列表缓存失效失败", user_id, exc)


async def create_conversation(
    session: AsyncSession,
    redis: Redis,
    title: str | None,
    user_id: int,
) -> Conversation:
    conversation = await ConversationRepository(session).create(title=title, user_id=user_id)
    await session.commit()
    await _invalidate_conversation_list_cache(redis, user_id)

    return conversation


async def list_conversations(
    session: AsyncSession,
    redis: Redis,
    user_id: int
) -> list[ConversationOut]:
    key = _conversation_list_cache_key(user_id)
    try:
        cached = await redis.get(key)
    except RedisError as exc:
        _log_cache_failure("读取会话列表缓存失败", user_id, exc)
        cached = None

    if cached is not None:
        try:
            return _deserialize_conversations(cached)
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            # 缓存内容不是事实来源：坏数据按 miss 处理，并尝试删除后重建。
            _log_cache_failure("解析会话列表缓存失败", user_id, exc)
            await _invalidate_conversation_list_cache(redis, user_id)

    rows = await ConversationRepository(
        session=session
    ).list_by_user_id(user_id)

    conversations = [
        ConversationOut.model_validate(row)
        for row in rows
    ]

    try:
        await redis.set(
            key,
            _serialize_conversations(conversations),
            ex=CONVERSATION_LIST_CACHE_TTL_SECONDS,
        )
    except RedisError as exc:
        _log_cache_failure("写入会话列表缓存失败", user_id, exc)

    return conversations


async def list_messages(
    session: AsyncSession,
    conversation_id: int,
    user_id: int,
) -> list[Message]:
    """返回会话全部历史；会话不存在时抛 ConversationNotFoundError。"""
    conversation = await ConversationRepository(session).get_by_id_for_user(conversation_id, user_id)
    if conversation is None:
        raise ConversationNotFoundError(conversation_id)

    return await MessageRepository(session).list_by_conversation(
        conversation_id=conversation_id,
        limit=None,
    )
