from sqlalchemy import select

from app.db.session import AsyncSessionFactory
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories import (
    conversation_repository,
    message_repository
)

async def test_conversation_create(fresh_schema):
    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        con = await conversation.create(title="对话 1")
        await session.commit()
        conversation_id = con.id

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        assert result.scalar_one().title == "对话 1"


async def test_conversation_list_all(fresh_schema):
    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        await conversation.create(title="对话 1")
        await conversation.create(title="对话 2")
        await session.commit()

    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        result = await conversation.list_all()
        assert [c.title for c in result] == ["对话 1", "对话 2"]


async def test_conversation_get_by_id(fresh_schema):
    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        con = await conversation.create(title="查找我")
        await session.commit()
        conversation_id = con.id

    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        found = await conversation.get_by_id(conversation_id)
        assert found is not None
        assert found.id == conversation_id
        assert found.title == "查找我"


async def test_conversation_get_by_id_missing(fresh_schema):
    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        assert await conversation.get_by_id(999999) is None


async def test_message_add(fresh_schema):
    async with AsyncSessionFactory() as session:
        conversations = conversation_repository.ConversationRepository(session)
        conv = await conversations.create(title="消息测试")
        messages = message_repository.MessageRepository(session)
        message = await messages.add(
            conversation_id=conv.id,
            role="user",
            content="你好",
        )
        await session.commit()
        message_id = message.id

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Message).where(Message.id == message_id)
        )
        found = result.scalar_one()
        assert found.role == "user"
        assert found.content == "你好"


async def test_message_list_by_conversation_ordered(fresh_schema):
    async with AsyncSessionFactory() as session:
        conversations = conversation_repository.ConversationRepository(session)
        conv = await conversations.create(title="历史测试")
        messages = message_repository.MessageRepository(session)
        await messages.add(conv.id, "user", "第一条")
        await messages.add(conv.id, "assistant", "回复一")
        await messages.add(conv.id, "user", "第二条")
        await session.commit()
        conversation_id = conv.id

    async with AsyncSessionFactory() as session:
        messages = message_repository.MessageRepository(session)
        history = await messages.list_by_conversation(conversation_id)
        assert [(m.role, m.content) for m in history] == [
            ("user", "第一条"),
            ("assistant", "回复一"),
            ("user", "第二条"),
        ]


async def test_message_list_returns_recent_limit(fresh_schema):
    # 语义：limit 应返回“最近的 N 条”（按 id 升序展示），而不是最早 N 条
    async with AsyncSessionFactory() as session:
        conversations = conversation_repository.ConversationRepository(session)
        conv = await conversations.create(title="limit 测试")
        messages = message_repository.MessageRepository(session)
        for i in range(25):
            await messages.add(conv.id, "user", f"消息 {i}")
        await session.commit()
        conversation_id = conv.id

    async with AsyncSessionFactory() as session:
        messages = message_repository.MessageRepository(session)
        recent = await messages.list_by_conversation(conversation_id, limit=20)
        assert len(recent) == 20
        assert recent[0].content == "消息 5"
        assert recent[-1].content == "消息 24"
