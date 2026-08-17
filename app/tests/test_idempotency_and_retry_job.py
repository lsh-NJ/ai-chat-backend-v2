import asyncio
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db.session import AsyncSessionFactory
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.retry_job import MessageRetryJob


def _retry_job_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": uuid4(),
        "idempotency_key": uuid4(),
        "conversation_id": 1,
        "user_id": 1,
        "content": "需要重试的消息",
        "is_complete": False,
        "attempt": 0,
    }
    payload.update(overrides)
    return payload


def test_retry_job_accepts_valid_payload() -> None:
    job = MessageRetryJob.model_validate(_retry_job_payload())

    assert isinstance(job.job_id, UUID)
    assert job.version == 1
    assert job.attempt == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 2},
        {"conversation_id": 0},
        {"user_id": -1},
        {"content": " \t\n"},
        {"attempt": -1},
    ],
)
def test_retry_job_rejects_invalid_payload(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MessageRetryJob.model_validate(_retry_job_payload(**overrides))


def test_retry_job_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MessageRetryJob.model_validate(_retry_job_payload(unexpected="拒绝"))


async def _create_conversation(user_id: int) -> int:
    async with AsyncSessionFactory() as session:
        conversation = await ConversationRepository(session).create(
            title="幂等测试",
            user_id=user_id,
        )
        await session.commit()
        return conversation.id


async def _insert_idempotent_message(
    conversation_id: int,
    idempotency_key: UUID,
    content: str,
) -> int:
    async with AsyncSessionFactory() as session:
        message = await MessageRepository(session).add_idempotent(
            conversation_id=conversation_id,
            role="user",
            content=content,
            is_complete=True,
            idempotency_key=idempotency_key,
        )
        await session.commit()
        return message.id


async def test_same_idempotency_key_keeps_first_message(
    fresh_schema,
    test_user_id: int,
) -> None:
    conversation_id = await _create_conversation(test_user_id)
    key = uuid4()

    async with AsyncSessionFactory() as session:
        first = await MessageRepository(session).add(
            conversation_id=conversation_id,
            role="user",
            content="第一次提交",
            is_complete=True,
            idempotency_key=key,
        )
        await session.commit()
        first_id = first.id

    second_id = await _insert_idempotent_message(
        conversation_id,
        key,
        "重复请求不应覆盖首次内容",
    )

    async with AsyncSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(Message))
        stored = await session.scalar(
            select(Message).where(Message.id == first_id)
        )

    assert second_id == first_id
    assert count == 1
    assert stored is not None
    assert stored.content == "第一次提交"


async def test_different_idempotency_keys_create_two_messages(
    fresh_schema,
    test_user_id: int,
) -> None:
    conversation_id = await _create_conversation(test_user_id)

    first_id = await _insert_idempotent_message(
        conversation_id,
        uuid4(),
        "第一条",
    )
    second_id = await _insert_idempotent_message(
        conversation_id,
        uuid4(),
        "第二条",
    )

    async with AsyncSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(Message))

    assert first_id != second_id
    assert count == 2


async def test_null_idempotency_keys_are_not_deduplicated(
    fresh_schema,
    test_user_id: int,
) -> None:
    conversation_id = await _create_conversation(test_user_id)

    async with AsyncSessionFactory() as session:
        repository = MessageRepository(session)
        first = await repository.add(
            conversation_id,
            "user",
            "没有幂等 key 的第一条",
            is_complete=True,
        )
        second = await repository.add(
            conversation_id,
            "user",
            "没有幂等 key 的第二条",
            is_complete=True,
        )
        await session.commit()

    assert first.id != second.id

    async with AsyncSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(Message))

    assert count == 2


async def test_concurrent_same_key_creates_one_message(
    fresh_schema,
    test_user_id: int,
) -> None:
    conversation_id = await _create_conversation(test_user_id)
    key = uuid4()

    message_ids = await asyncio.gather(
        _insert_idempotent_message(conversation_id, key, "并发请求 A"),
        _insert_idempotent_message(conversation_id, key, "并发请求 B"),
    )

    async with AsyncSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(Message))
        stored = await session.scalar(
            select(Message).where(Message.idempotency_key == key)
        )

    assert message_ids[0] == message_ids[1]
    assert count == 1
    assert stored is not None
    assert stored.content in {"并发请求 A", "并发请求 B"}
