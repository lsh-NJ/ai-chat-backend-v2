import json
import logging

from app.db.session import AsyncSessionFactory
from app.repositories.conversation_repository import ConversationRepository
from app.services import conversation_service


async def _create_conversation(
    *,
    redis_test_client,
    user_id: int,
    title: str,
) -> None:
    async with AsyncSessionFactory() as session:
        await conversation_service.create_conversation(
            session=session,
            redis=redis_test_client,
            title=title,
            user_id=user_id,
        )


async def _list_conversations(
    *,
    redis_test_client,
    user_id: int,
):
    async with AsyncSessionFactory() as session:
        return await conversation_service.list_conversations(
            session=session,
            redis=redis_test_client,
            user_id=user_id,
        )


async def test_first_list_reads_postgres_and_writes_real_redis(
    create_test_user,
    redis_test_client,
):
    user = await create_test_user("cache-reader")
    await _create_conversation(
        redis_test_client=redis_test_client,
        user_id=user.id,
        title="数据库中的会话",
    )

    conversations = await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=user.id,
    )

    assert [conversation.title for conversation in conversations] == [
        "数据库中的会话"
    ]

    key = conversation_service._conversation_list_cache_key(user.id)
    cached = await redis_test_client.get(key)
    assert cached is not None
    assert json.loads(cached)[0]["title"] == "数据库中的会话"


async def test_second_list_hits_cache_without_calling_repository(
    create_test_user,
    redis_test_client,
    monkeypatch,
):
    user = await create_test_user("cache-hitter")
    await _create_conversation(
        redis_test_client=redis_test_client,
        user_id=user.id,
        title="只查询一次数据库",
    )
    await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=user.id,
    )

    async def database_access_is_unexpected(*args, **kwargs):
        raise AssertionError("缓存命中时不应查询 PostgreSQL")

    monkeypatch.setattr(
        ConversationRepository,
        "list_by_user_id",
        database_access_is_unexpected,
    )

    conversations = await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=user.id,
    )

    assert [conversation.title for conversation in conversations] == [
        "只查询一次数据库"
    ]


async def test_create_invalidates_cached_list_and_next_read_rebuilds_it(
    create_test_user,
    redis_test_client,
):
    user = await create_test_user("cache-invalidator")
    key = conversation_service._conversation_list_cache_key(user.id)

    assert await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=user.id,
    ) == []
    assert await redis_test_client.get(key) is not None

    await _create_conversation(
        redis_test_client=redis_test_client,
        user_id=user.id,
        title="新会话",
    )
    assert await redis_test_client.get(key) is None

    conversations = await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=user.id,
    )
    assert [conversation.title for conversation in conversations] == ["新会话"]
    assert await redis_test_client.get(key) is not None


async def test_each_user_has_an_isolated_cache_key_and_content(
    create_test_user,
    redis_test_client,
):
    first_user = await create_test_user("cache-user-one")
    second_user = await create_test_user("cache-user-two")

    await _create_conversation(
        redis_test_client=redis_test_client,
        user_id=first_user.id,
        title="用户一的会话",
    )
    await _create_conversation(
        redis_test_client=redis_test_client,
        user_id=second_user.id,
        title="用户二的会话",
    )

    first_conversations = await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=first_user.id,
    )
    second_conversations = await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=second_user.id,
    )

    first_key = conversation_service._conversation_list_cache_key(first_user.id)
    second_key = conversation_service._conversation_list_cache_key(second_user.id)

    assert first_key != second_key
    assert [conversation.title for conversation in first_conversations] == [
        "用户一的会话"
    ]
    assert [conversation.title for conversation in second_conversations] == [
        "用户二的会话"
    ]
    assert "用户二的会话" not in await redis_test_client.get(first_key)
    assert "用户一的会话" not in await redis_test_client.get(second_key)


async def test_cached_list_has_a_bounded_ttl(
    create_test_user,
    redis_test_client,
):
    user = await create_test_user("cache-ttl")
    await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=user.id,
    )

    ttl = await redis_test_client.ttl(
        conversation_service._conversation_list_cache_key(user.id)
    )
    assert 0 < ttl <= 60


async def test_cached_list_contains_only_conversation_output_fields(
    create_test_user,
    redis_test_client,
):
    user = await create_test_user("cache-safe-payload")
    await _create_conversation(
        redis_test_client=redis_test_client,
        user_id=user.id,
        title="安全缓存",
    )
    await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=user.id,
    )

    cached = await redis_test_client.get(
        conversation_service._conversation_list_cache_key(user.id)
    )
    assert cached is not None

    payload = json.loads(cached)
    assert set(payload[0]) == {"id", "title", "created_at"}
    assert "password_hash" not in cached
    assert "access_token" not in cached
    assert "token" not in cached


async def test_invalid_cached_payload_falls_back_to_postgres_and_rebuilds_cache(
    create_test_user,
    redis_test_client,
    caplog,
    monkeypatch,
):
    user = await create_test_user("cache-corrupted-payload")
    await _create_conversation(
        redis_test_client=redis_test_client,
        user_id=user.id,
        title="缓存损坏后仍可读取",
    )
    key = conversation_service._conversation_list_cache_key(user.id)
    invalid_payload = "不应进入日志的损坏缓存内容"
    await redis_test_client.set(key, invalid_payload, ex=60)

    monkeypatch.setattr(conversation_service.logger, "disabled", False)
    monkeypatch.setattr(conversation_service.logger, "propagate", True)
    caplog.set_level(logging.WARNING, logger="app")

    conversations = await _list_conversations(
        redis_test_client=redis_test_client,
        user_id=user.id,
    )

    assert [conversation.title for conversation in conversations] == [
        "缓存损坏后仍可读取"
    ]
    rebuilt_payload = await redis_test_client.get(key)
    assert rebuilt_payload is not None
    assert json.loads(rebuilt_payload)[0]["title"] == "缓存损坏后仍可读取"
    assert any(
        "解析会话列表缓存失败" in record.getMessage()
        for record in caplog.records
    )
    assert invalid_payload not in caplog.text
