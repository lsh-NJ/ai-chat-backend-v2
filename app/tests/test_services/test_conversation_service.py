import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.exceptions import ConversationNotFoundError
from app.db.session import AsyncSessionFactory
from app.models.conversation import Conversation
from app.services import conversation_service


# 目标：创建会话提交后返回带 id 的 ORM 对象
async def test_create_conversation_commits_and_returns_orm(
    fresh_schema,
    test_user_id,
    redis_client,
):
    async with AsyncSessionFactory() as session:
        conversation = await conversation_service.create_conversation(
            session=session,
            redis=redis_client,
            title="新会话",
            user_id=test_user_id,
        )
        assert conversation.id is not None
        assert conversation.title == "新会话"


# 目标：列表返回全部会话
async def test_list_conversations_returns_all(
    fresh_schema,
    test_user_id,
    redis_client,
):
    async with AsyncSessionFactory() as session:
        await conversation_service.create_conversation(
            session=session,
            redis=redis_client,
            title="会话 1",
            user_id=test_user_id,
        )
        await conversation_service.create_conversation(
            session=session,
            redis=redis_client,
            title="会话 2",
            user_id=test_user_id,
        )

        conversations = await conversation_service.list_conversations(
            session=session,
            redis=redis_client,
            user_id=test_user_id,
        )
        assert [c.title for c in conversations] == ["会话 1", "会话 2"]


# 目标：数据库提交后缓存失效失败，不伪装成创建失败，也不记录敏感内容
async def test_create_conversation_survives_cache_invalidation_failure(
    fresh_schema,
    test_user_id,
    redis_client,
    caplog,
    monkeypatch,
):
    secret_title = "不应写入日志的会话标题"
    redis_client.delete.side_effect = RedisConnectionError(
        "不应写入日志的 Redis 异常详情"
    )

    monkeypatch.setattr(conversation_service.logger, "disabled", False)
    monkeypatch.setattr(conversation_service.logger, "propagate", True)
    caplog.set_level(logging.WARNING, logger="app")

    async with AsyncSessionFactory() as session:
        conversation = await conversation_service.create_conversation(
            session=session,
            redis=redis_client,
            title=secret_title,
            user_id=test_user_id,
        )

    assert conversation.id is not None

    async with AsyncSessionFactory() as session:
        saved_conversation = await session.get(Conversation, conversation.id)

    assert saved_conversation is not None
    assert saved_conversation.title == secret_title

    error_record = next(
        record
        for record in caplog.records
        if "会话列表缓存失效失败" in record.getMessage()
    )
    assert error_record.user_id == test_user_id
    assert error_record.error_type == "ConnectionError"
    assert secret_title not in caplog.text
    assert "不应写入日志的 Redis 异常详情" not in caplog.text


# 目标：Redis 读取失败时退回 PostgreSQL，不让缓存故障阻断列表接口
async def test_list_conversations_falls_back_to_postgres_when_cache_read_fails(
    fresh_schema,
    test_user_id,
    redis_client,
    caplog,
    monkeypatch,
):
    secret_title = "读取失败时仍应返回的会话"
    async with AsyncSessionFactory() as session:
        await conversation_service.create_conversation(
            session=session,
            redis=redis_client,
            title=secret_title,
            user_id=test_user_id,
        )

        redis_client.get.side_effect = RedisConnectionError(
            "不应写入日志的 Redis 读取异常详情"
        )
        monkeypatch.setattr(conversation_service.logger, "disabled", False)
        monkeypatch.setattr(conversation_service.logger, "propagate", True)
        caplog.set_level(logging.WARNING, logger="app")

        conversations = await conversation_service.list_conversations(
            session=session,
            redis=redis_client,
            user_id=test_user_id,
        )

    assert [conversation.title for conversation in conversations] == [secret_title]
    error_record = next(
        record
        for record in caplog.records
        if "读取会话列表缓存失败" in record.getMessage()
    )
    assert error_record.user_id == test_user_id
    assert error_record.error_type == "ConnectionError"
    assert secret_title not in caplog.text
    assert "不应写入日志的 Redis 读取异常详情" not in caplog.text


# 目标：Redis 写入失败时仍返回 PostgreSQL 结果，不让缓存成为读取的单点故障
async def test_list_conversations_returns_postgres_result_when_cache_write_fails(
    fresh_schema,
    test_user_id,
    redis_client,
    caplog,
    monkeypatch,
):
    secret_title = "写入失败时仍应返回的会话"
    async with AsyncSessionFactory() as session:
        await conversation_service.create_conversation(
            session=session,
            redis=redis_client,
            title=secret_title,
            user_id=test_user_id,
        )

        redis_client.set.side_effect = RedisConnectionError(
            "不应写入日志的 Redis 写入异常详情"
        )
        monkeypatch.setattr(conversation_service.logger, "disabled", False)
        monkeypatch.setattr(conversation_service.logger, "propagate", True)
        caplog.set_level(logging.WARNING, logger="app")

        conversations = await conversation_service.list_conversations(
            session=session,
            redis=redis_client,
            user_id=test_user_id,
        )

    assert [conversation.title for conversation in conversations] == [secret_title]
    error_record = next(
        record
        for record in caplog.records
        if "写入会话列表缓存失败" in record.getMessage()
    )
    assert error_record.user_id == test_user_id
    assert error_record.error_type == "ConnectionError"
    assert secret_title not in caplog.text
    assert "不应写入日志的 Redis 写入异常详情" not in caplog.text


# 目标：不存在的会话查历史 → 抛 ConversationNotFoundError
async def test_list_messages_missing_conversation_raises_not_found(
    fresh_schema,
    test_user_id,
):
    async with AsyncSessionFactory() as session:
        with pytest.raises(ConversationNotFoundError):
            await conversation_service.list_messages(
                session=session,
                conversation_id=999999,
                user_id=test_user_id,
            )
