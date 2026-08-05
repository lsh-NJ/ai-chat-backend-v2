from app.models.message import Message

from sqlalchemy import select, Result
from sqlalchemy.ext.asyncio import AsyncSession

class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session: AsyncSession = session

    # 添加对话历史
    async def add(self, conversation_id: int, role: str, content: str) -> Message:
        message = Message(
            conversation_id=conversation_id, 
            role=role, 
            content=content
        )
        self.session.add(message)
        await self.session.flush()
        return message

    # 查询会话的 limit 条最近历史
    async def list_by_conversation(
        self,
        conversation_id: int,
        limit: int | None = 20,
    ) -> list[Message]:
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
        )
        if limit is not None:
            query = query.limit(limit)

        result: Result = await self.session.execute(query)
        return list(reversed(result.scalars().all()))
