from uuid import UUID

from app.models.message import Message

from sqlalchemy import select, Result
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession


IDEMPOTENCY_CONSTRAINT = "uq_messages_idempotency_key"


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session: AsyncSession = session

    # 添加对话历史
    async def add(
        self,
        conversation_id: int,
        role: str,
        content: str,
        is_complete: bool,
        idempotency_key: UUID | None = None,
    ) -> Message:
        if idempotency_key is not None:
            return await self.add_idempotent(
                conversation_id=conversation_id,
                role=role,
                content=content,
                is_complete=is_complete,
                idempotency_key=idempotency_key,
            )

        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            is_complete=is_complete,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def add_idempotent(
        self,
        conversation_id: int,
        role: str,
        content: str,
        is_complete: bool,
        idempotency_key: UUID,
    ) -> Message:
        """使用 idempotency_key 写入消息，保证同一个业务操作重复执行时，数据库只产生一条消息"""
        statement = (
            insert(Message)
            .values(
                conversation_id=conversation_id,
                role=role,
                content=content,
                is_complete=is_complete,
                idempotency_key=idempotency_key,
            )
            .on_conflict_do_nothing(constraint=IDEMPOTENCY_CONSTRAINT)
            .returning(Message)
        )
        result = await self.session.execute(statement)
        created = result.scalar_one_or_none()
        if created is not None:
            return created

        existing_result = await self.session.execute(
            select(Message).where(
                Message.idempotency_key == idempotency_key,
            )
        )
        return existing_result.scalar_one()

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
