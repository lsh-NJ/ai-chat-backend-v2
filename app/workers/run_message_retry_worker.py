"""Process entrypoint for the Redis Stream message retry Worker."""

import asyncio
import logging
import os
import signal
import socket

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.db.redis import close_redis, create_redis_client
from app.db.session import close_db
from app.queue.message_retry_queue import ensure_consumer_group
from app.workers.message_retry_worker import run_once

logger = logging.getLogger("app")

INITIAL_RETRY_BACKOFF_SECONDS = 0.5
MAX_RETRY_BACKOFF_SECONDS = 5.0


def build_consumer_name() -> str:
    """Return a stable explicit name or a process-unique default name."""
    configured_name = os.getenv("WORKER_CONSUMER_NAME", "").strip()
    if configured_name:
        return configured_name
    return f"{socket.gethostname()}-{os.getpid()}"


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Translate process stop signals into a cooperative stop request."""
    loop = asyncio.get_running_loop()
    for received_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(received_signal, stop_event.set)


async def run_forever(
    redis: Redis,
    consumer_name: str,
    stop_event: asyncio.Event,
    *,
    initial_backoff_seconds: float = INITIAL_RETRY_BACKOFF_SECONDS,
    max_backoff_seconds: float = MAX_RETRY_BACKOFF_SECONDS,
) -> None:
    """Run finite polling rounds until a cooperative stop is requested."""
    if initial_backoff_seconds <= 0:
        raise ValueError("initial_backoff_seconds must be positive")
    if max_backoff_seconds < initial_backoff_seconds:
        raise ValueError(
            "max_backoff_seconds must be at least initial_backoff_seconds"
        )

    claim_start_id = "0-0"
    backoff_seconds = initial_backoff_seconds
    while not stop_event.is_set():
        try:
            claim_start_id, _ = await run_once(
                redis,
                consumer_name,
                claim_start_id=claim_start_id,
                stop_event=stop_event,
            )
            backoff_seconds = initial_backoff_seconds
        except RedisError as exc:
            logger.error(
                "retry worker Redis operation failed",
                extra={
                    "attempt": 0,
                    "status": "redis_backoff",
                    "error_type": type(exc).__name__,
                },
            )
            await _wait_for_stop(stop_event, backoff_seconds)
            backoff_seconds = min(
                backoff_seconds * 2,
                max_backoff_seconds,
            )


async def _wait_for_stop(
    stop_event: asyncio.Event,
    timeout_seconds: float,
) -> None:
    """Wait for shutdown without making backoff delay shutdown responsiveness."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout_seconds)
    except TimeoutError:
        pass


async def async_main() -> None:
    """Own Worker dependencies, signals, startup checks and cleanup."""
    redis_url = os.environ["REDIS_URL"]
    consumer_name = build_consumer_name()
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)
    redis = create_redis_client(redis_url)

    try:
        # Startup is fail-closed so the process manager can expose/restart a bad deployment.
        await redis.ping()
        await ensure_consumer_group(redis)
        logger.info(
            "retry worker started",
            extra={
                "attempt": 0,
                "status": "worker_started",
                "consumer_name": consumer_name,
            },
        )
        await run_forever(redis, consumer_name, stop_event)
    finally:
        await close_redis(redis)
        await close_db()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
