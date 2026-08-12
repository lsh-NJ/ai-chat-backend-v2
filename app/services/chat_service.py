import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from collections.abc import AsyncIterator

from app.services.llm_service import call_llm, stream_llm
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.chat import ChatResponse
from app.core.exceptions import LLMServiceError, ConversationNotFoundError
from app.services.conversation_service import create_conversation


async def save_message(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    content: str
) -> int:
    message = MessageRepository(session)
    mes = await message.add(
        conversation_id=conversation_id,
        role=role,
        content=content,
    )
    await session.commit()
    return mes.id


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
) -> ChatResponse:
    if conversation_id is None:
        con = await create_conversation(
            session,
            message[:30],
            user_id,
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
):
    if conversation_id is None:
        con = await create_conversation(
            session,
            message[:30],
            user_id,
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
        try:
            # async iterator 需要 async for
            async for chunk in llm_chunks:
                fully_parts.append(chunk)
                yield chunk

        except LLMServiceError as e:
            fully_parts.append(f"\n\n[流式响应中断：{e}]\n")

        finally:
            fully_reply = "".join(fully_parts).strip()
            if fully_reply:
                await save_message(
                    session=session,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=fully_reply,
                )

    return conversation_id, generate()
    
