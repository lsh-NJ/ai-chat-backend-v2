import asyncio
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

import app.workers.run_message_retry_worker as runner


async def test_run_forever_retries_redis_error_with_backoff(monkeypatch) -> None:
    stop_event = asyncio.Event()
    redis = AsyncMock()
    call_count = 0

    async def fail_once_then_stop(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RedisConnectionError("redis unavailable")
        stop_event.set()
        return "0-0", 0

    monkeypatch.setattr(runner, "run_once", fail_once_then_stop)

    await runner.run_forever(
        redis,
        consumer_name="test-worker",
        stop_event=stop_event,
        initial_backoff_seconds=0.001,
        max_backoff_seconds=0.002,
    )

    assert call_count == 2


async def test_run_forever_propagates_unknown_error(monkeypatch) -> None:
    async def fail_with_programming_error(*args, **kwargs):
        raise TypeError("runner implementation bug")

    monkeypatch.setattr(runner, "run_once", fail_with_programming_error)

    with pytest.raises(TypeError, match="runner implementation bug"):
        await runner.run_forever(
            AsyncMock(),
            consumer_name="test-worker",
            stop_event=asyncio.Event(),
        )


async def test_async_main_closes_redis_and_database(monkeypatch) -> None:
    redis = AsyncMock()
    close_redis = AsyncMock()
    close_db = AsyncMock()
    ensure_group = AsyncMock()
    run_forever = AsyncMock()

    monkeypatch.setenv("REDIS_URL", "redis://test.invalid:6379/0")
    monkeypatch.setattr(runner, "create_redis_client", lambda url: redis)
    monkeypatch.setattr(runner, "close_redis", close_redis)
    monkeypatch.setattr(runner, "close_db", close_db)
    monkeypatch.setattr(runner, "ensure_consumer_group", ensure_group)
    monkeypatch.setattr(runner, "run_forever", run_forever)
    monkeypatch.setattr(runner, "install_signal_handlers", lambda event: None)

    await runner.async_main()

    redis.ping.assert_awaited_once_with()
    ensure_group.assert_awaited_once_with(redis)
    run_forever.assert_awaited_once()
    close_redis.assert_awaited_once_with(redis)
    close_db.assert_awaited_once_with()
