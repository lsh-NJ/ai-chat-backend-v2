import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.session import AsyncSessionFactory
from app.models.message import Message
from app.repositories.message_repository import MessageRepository


# 目标：外键约束拒绝孤儿消息；失败事务回滚后不留脏数据
async def test_message_fk_violation_is_rejected(fresh_schema):
    async with AsyncSessionFactory() as session:
        messages = MessageRepository(session)

        with pytest.raises(IntegrityError):
            await messages.add(
                conversation_id=-1,
                role="user",
                content="没有父会话的消息",
                is_complete=True,
            )

        # 事务已失败，必须先回滚，否则这个 session 后续任何操作都会报
        # InFailedSqlTransaction（当前事务已中止）
        await session.rollback()

    # 换个新 session 确认：没有留下任何脏数据
    async with AsyncSessionFactory() as session:
        count = (
            await session.execute(select(func.count()).select_from(Message))
        ).scalar_one()
        assert count == 0
