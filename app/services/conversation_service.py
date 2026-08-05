from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConversationNotFoundError
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository


async def create_conversation(
    session: AsyncSession,
    title: str | None,
) -> Conversation:
    """创建会话并提交短事务，返回 ORM 对象。"""
    conversation = await ConversationRepository(session).create(title=title)
    await session.commit()
    return conversation


async def list_conversations(
    session: AsyncSession,
) -> list[Conversation]:
    return await ConversationRepository(session).list_all()


async def list_messages(
    session: AsyncSession,
    conversation_id: int,
) -> list[Message]:
    """返回会话全部历史；会话不存在时抛 ConversationNotFoundError。"""
    conversation = await ConversationRepository(session).get_by_id(conversation_id)
    if conversation is None:
        raise ConversationNotFoundError(conversation_id)

    return await MessageRepository(session).list_by_conversation(
        conversation_id=conversation_id,
        limit=None,
    )
