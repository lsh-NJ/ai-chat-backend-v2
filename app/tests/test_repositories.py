from sqlalchemy import select

from app.db.session import AsyncSessionFactory
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories import (
    conversation_repository,
    message_repository
)

# 目标：conversation_repository.create 落库后，新 session 能查回
async def test_conversation_create(fresh_schema, test_user_id):
    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        con = await conversation.create(title="对话 1", user_id=test_user_id)
        await session.commit()
        conversation_id = con.id

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        assert result.scalar_one().title == "对话 1"


# 目标：list_all 返回全部会话且顺序正确
async def test_conversation_list_all(fresh_schema, test_user_id):
    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        await conversation.create(title="对话 1", user_id=test_user_id)
        await conversation.create(title="对话 2", user_id=test_user_id)
        await session.commit()

    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        result = await conversation.list_by_user_id(test_user_id)
        assert [c.title for c in result] == ["对话 1", "对话 2"]


# 目标：按 id 能查到已创建的会话
async def test_conversation_get_by_id(fresh_schema, test_user_id):
    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        con = await conversation.create(title="查找我", user_id=test_user_id)
        await session.commit()
        conversation_id = con.id

    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        found = await conversation.get_by_id_for_user(conversation_id, test_user_id)
        assert found is not None
        assert found.id == conversation_id
        assert found.title == "查找我"


# 目标：不存在的 id 返回 None（而不是抛异常）
async def test_conversation_get_by_id_missing(fresh_schema, test_user_id):
    async with AsyncSessionFactory() as session:
        conversation = conversation_repository.ConversationRepository(session)
        assert await conversation.get_by_id_for_user(999999, test_user_id) is None


# 目标：message_repository.add 落库后字段可查回
async def test_message_add(fresh_schema, test_user_id):
    async with AsyncSessionFactory() as session:
        conversations = conversation_repository.ConversationRepository(session)
        conv = await conversations.create(title="消息测试", user_id=test_user_id)
        messages = message_repository.MessageRepository(session)
        message = await messages.add(
            conversation_id=conv.id,
            role="user",
            content="你好",
            is_complete=True,
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


# 目标：历史消息按 id 升序返回（先到先出）
async def test_message_list_by_conversation_ordered(fresh_schema, test_user_id):
    async with AsyncSessionFactory() as session:
        conversations = conversation_repository.ConversationRepository(session)
        conv = await conversations.create(title="历史测试", user_id=test_user_id)
        messages = message_repository.MessageRepository(session)
        await messages.add(conv.id, "user", "第一条", is_complete=True)
        await messages.add(conv.id, "assistant", "回复一", is_complete=True)
        await messages.add(conv.id, "user", "第二条", is_complete=True)
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


# 目标：limit 返回「最近的 N 条」（升序展示），而不是最早 N 条
async def test_message_list_returns_recent_limit(fresh_schema, test_user_id):
    # 语义：limit 应返回“最近的 N 条”（按 id 升序展示），而不是最早 N 条
    async with AsyncSessionFactory() as session:
        conversations = conversation_repository.ConversationRepository(session)
        conv = await conversations.create(title="limit 测试", user_id=test_user_id)
        messages = message_repository.MessageRepository(session)
        for i in range(25):
            await messages.add(
                conv.id,
                "user",
                f"消息 {i}",
                is_complete=True,
            )
        await session.commit()
        conversation_id = conv.id

    async with AsyncSessionFactory() as session:
        messages = message_repository.MessageRepository(session)
        recent = await messages.list_by_conversation(conversation_id, limit=20)
        assert len(recent) == 20
        assert recent[0].content == "消息 5"
        assert recent[-1].content == "消息 24"
