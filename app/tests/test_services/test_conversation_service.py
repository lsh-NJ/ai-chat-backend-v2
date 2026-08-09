import pytest

from app.core.exceptions import ConversationNotFoundError
from app.db.session import AsyncSessionFactory
from app.services import conversation_service


# 目标：创建会话提交后返回带 id 的 ORM 对象
async def test_create_conversation_commits_and_returns_orm(fresh_schema):
    async with AsyncSessionFactory() as session:
        conversation = await conversation_service.create_conversation(
            session=session,
            title="新会话",
        )
        assert conversation.id is not None
        assert conversation.title == "新会话"


# 目标：列表返回全部会话
async def test_list_conversations_returns_all(fresh_schema):
    async with AsyncSessionFactory() as session:
        await conversation_service.create_conversation(session, "会话 1")
        await conversation_service.create_conversation(session, "会话 2")

        conversations = await conversation_service.list_conversations(session)
        assert [c.title for c in conversations] == ["会话 1", "会话 2"]


# 目标：不存在的会话查历史 → 抛 ConversationNotFoundError
async def test_list_messages_missing_conversation_raises_not_found(fresh_schema):
    async with AsyncSessionFactory() as session:
        with pytest.raises(ConversationNotFoundError):
            await conversation_service.list_messages(
                session=session,
                conversation_id=999999,
            )
