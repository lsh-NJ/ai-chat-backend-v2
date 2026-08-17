import logging
from collections.abc import AsyncIterator

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_service import call_llm, stream_llm
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.chat import ChatResponse
from app.core.exceptions import LLMServiceError, ConversationNotFoundError
from app.services.conversation_service import create_conversation

logger = logging.getLogger("app")

async def save_message(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    is_complete: bool,
) -> int:
    try:
        message = MessageRepository(session)
        saved_message = await message.add(
            conversation_id=conversation_id,
            role=role,
            content=content,
            is_complete=is_complete,
        )
        await session.commit()
        return saved_message.id
    except Exception:
        await session.rollback()
        raise


async def get_history_message(
    session: AsyncSession,
    conversation_id: int, 
    limit: int = 20,
) -> list[dict[str, str]]:
    message = MessageRepository(session)
    messages: list[Message] = await message.list_by_conversation(conversation_id, limit)
    return [
        {
            "role": message.role,
            "content":message.content,
        }

        for message in messages
    ]

async def chat(
    client: httpx.AsyncClient,
    session: AsyncSession,
    conversation_id: int | None,
    message: str,
    user_id: int,
    redis: Redis,
) -> ChatResponse:
    if conversation_id is None:
        con = await create_conversation(
            session=session,
            redis=redis,
            title=message[:30],
            user_id=user_id,
        )
        conversation_id = con.id

    if conversation_id is not None:
        conversation = await ConversationRepository(session).get_by_id_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)

    await save_message(
        session=session,
        conversation_id=conversation_id,
        role= "user",
        content=message,
        is_complete=True,
    )

    history_messages = await get_history_message(
        session=session,
        conversation_id=conversation_id,
        limit=20,
    )

    reply = await call_llm(
        client=client,
        messages=history_messages,
    )
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
    client: httpx.AsyncClient,
    session: AsyncSession,
    conversation_id: int | None,
    message: str,
    user_id: int,
    redis: Redis,
):
    if conversation_id is None:
        con = await create_conversation(
            session=session,
            redis=redis,
            title=message[:30],
            user_id=user_id,
        )
        conversation_id = con.id

    if conversation_id is not None:
        conversation = await ConversationRepository(session).get_by_id_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)

    await save_message(
        session=session,
        conversation_id=conversation_id,
        role= "user",
        content=message,
        is_complete=True,
    )

    history_messages = await get_history_message(
        session=session,
        conversation_id=conversation_id,
        limit=20,
    )

    # async generator 不能 await
    llm_chunks: AsyncIterator[str] = stream_llm(
        client=client,
        messages=history_messages,
    )

    fully_parts: list[str] = []

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
                try:
                    await save_message(
                        session=session,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=fully_reply,
                        is_complete=completed_normally,
                    )
                except Exception as exc:
                    logger.error(
                        "保存流式 assistant 消息失败",
                        extra={
                            "conversation_id": conversation_id,
                            "user_id": user_id,
                            "is_complete": completed_normally,
                            "content_length": len(fully_reply),
                            "error_type": type(exc).__name__,
                        },
                    )

    return conversation_id, generate()
    
