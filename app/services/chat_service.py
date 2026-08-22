import logging
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConversationNotFoundError, LLMServiceError
from app.llm.context import ContextSelector
from app.llm.contracts import LLMMessage, LLMProvider, LLMRole
from app.models.message import Message
from app.queue.message_retry_queue import enqueue_retry_job
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.chat import ChatResponse
from app.schemas.retry_job import MessageRetryJob
from app.services.conversation_service import create_conversation

logger = logging.getLogger("app")

SYSTEM_MESSAGE = LLMMessage(
    role=LLMRole.SYSTEM,
    content="你是一个简洁、友好、可靠的 AI 助手。",
)

async def save_message(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    is_complete: bool,
    idempotency_key: UUID | None = None,
) -> int:
    try:
        message = MessageRepository(session)
        saved_message = await message.add(
            conversation_id=conversation_id,
            role=role,
            content=content,
            is_complete=is_complete,
            idempotency_key=idempotency_key,
        )
        await session.commit()
        return saved_message.id
    except Exception:
        await session.rollback()
        raise


async def persist_or_enqueue_assistant(
    *,
    session: AsyncSession,
    redis: Redis,
    conversation_id: int,
    user_id: int,
    content: str,
    is_complete: bool,
    job_id: UUID,
    idempotency_key: UUID,
) -> None:
    """保存 assistant 消息；数据库失败时投递可幂等重试的任务。

    流式响应已经开始发送后，持久化失败不能再改变客户端已经收到的内容。
    因此已知的数据库/Redis 基础设施失败只记录脱敏日志，不向生成器继续抛出异常。
    未知异常继续向外传播，避免把代码缺陷伪装成暂时性基础设施故障。
    """
    try:
        await save_message(
            session=session,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            is_complete=is_complete,
            idempotency_key=idempotency_key,
        )
        return
    except SQLAlchemyError as save_exc:
        save_error_type = type(save_exc).__name__

    job = MessageRetryJob(
        job_id=job_id,
        idempotency_key=idempotency_key,
        conversation_id=conversation_id,
        user_id=user_id,
        content=content,
        is_complete=is_complete,
        attempt=0,
    )

    try:
        await enqueue_retry_job(redis, job)
    except RedisError as enqueue_exc:
        logger.error(
            "保存流式 assistant 消息失败，重试任务入队也失败",
            extra={
                "job_id": str(job_id),
                "conversation_id": conversation_id,
                "attempt": 0,
                "status": "retry_enqueue_failed",
                "error_type": type(enqueue_exc).__name__,
                "save_error_type": save_error_type,
            },
        )
        return

    logger.warning(
        "保存流式 assistant 消息失败，已加入重试队列",
        extra={
            "job_id": str(job_id),
            "conversation_id": conversation_id,
            "attempt": 0,
            "status": "retry_enqueued",
            "error_type": save_error_type,
        },
    )


async def get_history_message(
    session: AsyncSession,
    conversation_id: int,
    before_id: int,
) -> list[LLMMessage]:
    message = MessageRepository(session)
    messages: list[Message] = await message.list_by_conversation(
        conversation_id,
        limit=None,
        before_id=before_id,
    )
    return [
        LLMMessage(role=LLMRole(message.role), content=message.content)
        for message in messages
    ]


async def build_chat_context(
    *,
    selector: ContextSelector,
    session: AsyncSession,
    conversation_id: int | None,
    content: str,
    user_id: int,
    redis: Redis,
) -> tuple[int, tuple[LLMMessage, ...]]:
    """Authorize, persist the current input, then build its bounded context."""
    current = LLMMessage(role=LLMRole.USER, content=content)

    if conversation_id is not None:
        conversation = await ConversationRepository(session).get_by_id_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)

    # Validate required input before creating a conversation or writing a message.
    selector.select(system=SYSTEM_MESSAGE, history=(), current=current)

    if conversation_id is None:
        conversation = await create_conversation(
            session=session,
            redis=redis,
            title=content[:30],
            user_id=user_id,
        )
        conversation_id = conversation.id

    current_message_id = await save_message(
        session=session,
        conversation_id=conversation_id,
        role="user",
        content=content,
        is_complete=True,
    )
    history = await get_history_message(
        session=session,
        conversation_id=conversation_id,
        before_id=current_message_id,
    )
    selection = selector.select(
        system=SYSTEM_MESSAGE,
        history=history,
        current=current,
    )
    return conversation_id, selection.messages


async def chat(
    provider: LLMProvider,
    context_selector: ContextSelector,
    session: AsyncSession,
    conversation_id: int | None,
    message: str,
    user_id: int,
    redis: Redis,
) -> ChatResponse:
    conversation_id, context = await build_chat_context(
        selector=context_selector,
        session=session,
        conversation_id=conversation_id,
        content=message,
        user_id=user_id,
        redis=redis,
    )

    reply = await provider.complete(context)
    await save_message(
        session=session,
        conversation_id=conversation_id,
        role="assistant",
        content=reply,
        is_complete=True,
    )

    return ChatResponse(
        conversation_id=conversation_id,
        reply=reply,
    )


async def chat_stream(
    provider: LLMProvider,
    context_selector: ContextSelector,
    session: AsyncSession,
    conversation_id: int | None,
    message: str,
    user_id: int,
    redis: Redis,
):
    conversation_id, context = await build_chat_context(
        selector=context_selector,
        session=session,
        conversation_id=conversation_id,
        content=message,
        user_id=user_id,
        redis=redis,
    )

    # async generator 不能 await
    llm_chunks: AsyncIterator[str] = provider.stream(context)

    fully_parts: list[str] = []
    job_id = uuid4()
    idempotency_key = uuid4()

    # async iterator 经过 for 循环使用后不能再次使用，因此用函数再次创造一遍
    async def generate():
        completed_normally = False
        try:
            # async iterator 需要 async for
            async for chunk in llm_chunks:
                fully_parts.append(chunk)
                yield chunk

            completed_normally = True

        except LLMServiceError as e:
            fully_parts.append(f"\n\n[流式响应中断：{e}]\n")

        finally:
            fully_reply = "".join(fully_parts).strip()
            if fully_reply:
                await persist_or_enqueue_assistant(
                    session=session,
                    redis=redis,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    content=fully_reply,
                    is_complete=completed_normally,
                    job_id=job_id,
                    idempotency_key=idempotency_key,
                )

    return conversation_id, generate()
    
