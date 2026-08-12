from sqlalchemy import select, Result

from app.models.conversation import Conversation
from sqlalchemy.ext.asyncio import AsyncSession

class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session: AsyncSession = session

    # 创建对话
    async def create(self, title: str | None, user_id: int) -> Conversation:
        conversation = Conversation(title=title, user_id=user_id)
        self.session.add(conversation)
        await self.session.flush()

        if conversation.id is None:
            raise RuntimeError(
                "创建分配 id 失败"
            )

        return conversation

    # 获取所有会话
    async def list_by_user_id(self, user_id: int) -> list[Conversation]:
        result: Result = await self.session.execute(
            select(Conversation)
            .where(Conversation.user_id==user_id)
            .order_by(Conversation.id)
        )
        return list(result.scalars().all())

    # 获得 id 对应会话
    async def get_by_id_for_user(self, conversation_id: int, user_id: int) -> Conversation | None:
        result: Result = await self.session.execute(
            select(Conversation)
            .where(
                (Conversation.id==conversation_id) &
                (Conversation.user_id==user_id)
            )
        )
        return result.scalar_one_or_none()

