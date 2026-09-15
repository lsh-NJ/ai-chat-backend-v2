"""RAG ingestion worker 的进程入口（Week 17 Day 2）。"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from app.db.session import close_db
from app.rag.embedding import create_embedder_from_env
from app.rag.ingestion_processor import PostgresIngestionProcessor
from app.rag.upload_storage import LocalFileStorage
from app.workers.rag_ingestion_worker import RagIngestionWorker, run_forever

logger = logging.getLogger("app")


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for received_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(received_signal, stop_event.set)


async def async_main() -> None:
    """组装依赖并运行 worker，直到收到停止信号。"""
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)

    worker = RagIngestionWorker(
        processor=PostgresIngestionProcessor(
            storage=LocalFileStorage.from_env(),
            embedder=create_embedder_from_env(),
        ),
        lease_seconds=int(os.environ.get("RAG_INGESTION_LEASE_SECONDS", "300")),
    )

    logger.info("rag ingestion worker started", extra={"status": "started"})
    try:
        await run_forever(
            worker,
            stop_event,
            poll_interval_seconds=float(
                os.environ.get("RAG_INGESTION_POLL_SECONDS", "1.0")
            ),
        )
    finally:
        await close_db()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
