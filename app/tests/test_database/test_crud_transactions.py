from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionFactory
from app.models.conversation import Conversation
from app.models.message import Message


# 目标：flush 先拿到 id，commit 后数据持久化，新 session 能查回
async def test_create_flush_commit_select(fresh_schema, test_user_id):
    async with AsyncSessionFactory() as session:
        conv = Conversation(title="Day2 测试", user_id=test_user_id)
        session.add(conv)
        await session.flush()
        assert conv.id is not None

        session.add(Message(
            conversation_id=conv.id,
            role="user",
            content="你好",
            is_complete=True,
        ))
        await session.commit()
        conv_id = conv.id

    async with AsyncSessionFactory() as session:
        found = await session.get(
            Conversation, 
            conv_id,
            options=[selectinload(Conversation.messages)],
        )
        assert found is not None
        assert found.title == "Day2 测试"
        assert found.messages[0].content == "你好"


# 目标：rollback 丢弃未提交的数据，新 session 查不到
async def test_rollback_discards_uncommitted(fresh_schema, test_user_id):
    async with AsyncSessionFactory() as session:
        tmp = Conversation(title="待回滚", user_id=test_user_id)
        session.add(tmp)
        await session.flush()
        assert await session.get(Conversation, tmp.id) is not None
        await session.rollback()

    async with AsyncSessionFactory() as session:
        assert await session.get(Conversation, tmp.id) is None
