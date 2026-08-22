import pytest
from sqlalchemy import select

from app.core.exceptions import LLMResponseFormatError
from app.db.session import AsyncSessionFactory
from app.llm.contracts import LLMRole
from app.llm.retry import RetryPolicy
from app.models.conversation import Conversation
from app.models.message import Message
from app.services import structured_service


async def _messages_of(session, conversation_id: int) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list(result.scalars().all())


async def test_extract_saves_user_message_and_returns_validated_result(
    fresh_schema,
    test_user_id,
    redis_client,
    llm_provider,
):
    async with AsyncSessionFactory() as session:
        conversation_id, result = await structured_service.extract_topic_sentiment(
            provider=llm_provider,
            session=session,
            redis=redis_client,
            user_id=test_user_id,
            text="AI is great",
        )

    assert result == {"topic": "AI", "sentiment": "positive"}
    assert len(llm_provider.structured_calls) == 1

    sent_messages, sent_schema = llm_provider.structured_calls[0]
    assert sent_messages[0].role == LLMRole.SYSTEM
    assert "JSON Schema" in sent_messages[0].content
    assert sent_messages[-1].content == "AI is great"
    assert sent_schema == structured_service.EXTRACT_SCHEMA

    async with AsyncSessionFactory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        messages = await _messages_of(session, conversation_id)
        assert [(m.role, m.content) for m in messages] == [("user", "AI is great")]


async def test_extract_keeps_user_message_when_structured_fails(
    fresh_schema,
    test_user_id,
    redis_client,
    llm_provider,
):
    async def fail_structured(messages, schema):
        raise LLMResponseFormatError("bad json")

    llm_provider.structured_handler = fail_structured
    no_wait_policy = RetryPolicy(
        max_attempts=1,
        base_delay_seconds=0,
        max_delay_seconds=0,
    )

    with pytest.raises(LLMResponseFormatError, match="bad json"):
        async with AsyncSessionFactory() as session:
            await structured_service.extract_topic_sentiment(
                provider=llm_provider,
                session=session,
                redis=redis_client,
                user_id=test_user_id,
                text="AI is confusing",
                retry_policy=no_wait_policy,
            )

    async with AsyncSessionFactory() as session:
        conversation = (await session.execute(select(Conversation))).scalars().first()
        assert conversation is not None
        messages = await _messages_of(session, conversation.id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", "AI is confusing")
        ]
