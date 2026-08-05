from app.models.conversation import Conversation

from sqlalchemy import select, Result
from sqlalchemy.ext.asyncio import AsyncSession

class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session: AsyncSession = session

    # 创建对话
    async def create(self, title: str | None) -> Conversation:
        conversation = Conversation(title=title)
        self.session.add(conversation)
        await self.session.flush()

        if conversation.id is None:
            raise RuntimeError(
                "创建分配 id 失败"
            )

        return conversation

    # 获取所有会话
    async def list_all(self) -> list[Conversation]:
        result: Result = await self.session.execute(
            select(Conversation)
            .order_by(Conversation.id)
        )
        return list(result.scalars().all())

    # 获得 id 对应会话
    async def get_by_id(self, conversation_id: int) -> Conversation | None:
        result: Result = await self.session.execute(
            select(Conversation).where(Conversation.id==conversation_id)
        )
        return result.scalar_one_or_none()

