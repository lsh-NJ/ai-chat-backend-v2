"""RAG ingestion worker（Week 17 Day 2）。

职责：
- 从 PostgreSQL 领取 pending 任务；
- 调用 processor 执行真正的解析/切分/embedding；
- 成功推进为 succeeded，失败按 attempts 决定重试还是 failed；
- 回收超过租约仍处于 running 的僵尸任务。

不负责：
- 解析和 embedding 细节（在 `PostgresIngestionProcessor`）；
- HTTP 语义（在 API 层）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionFactory
from app.rag.ingestion_job import IngestionJob
from app.rag.ingestion_processor import IngestionProcessor
from app.repositories.rag_ingestion_job_worker_repository import (
    RagIngestionJobWorkerRepository,
)

logger = logging.getLogger("app")

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class RagIngestionWorker:
    """单进程 ingestion worker 的最小实现。"""

    def __init__(
        self,
        *,
        processor: IngestionProcessor,
        session_factory: SessionFactory = AsyncSessionFactory,
        lease_seconds: int = 300,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._processor = processor
        self._session_factory = session_factory
        self._lease_seconds = lease_seconds

    async def recover_stale_jobs(self) -> int:
        """把租约过期的 running 任务恢复为 pending 或 failed。"""
        async with self._session_factory() as session:
            repository = RagIngestionJobWorkerRepository(session)
            recovered = await repository.recover_stale_running(
                lease_seconds=self._lease_seconds,
            )
            await session.commit()
            return recovered

    async def run_once(self) -> bool:
        """处理一个任务；没有可领取任务时返回 False。"""
        job = await self._claim_one()
        if job is None:
            return False

        try:
            async with self._session_factory() as session:
                await self._processor.process(session, job)
        except Exception as exc:
            await self._handle_failure(job, exc)
            return True

        async with self._session_factory() as session:
            repository = RagIngestionJobWorkerRepository(session)
            await repository.mark_succeeded(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
            )
            await session.commit()
        return True

    async def _claim_one(self) -> IngestionJob | None:
        async with self._session_factory() as session:
            repository = RagIngestionJobWorkerRepository(session)
            job = await repository.claim_next()
            await session.commit()
            return job

    async def _handle_failure(self, job: IngestionJob, exc: Exception) -> None:
        error_message = f"{type(exc).__name__}: {exc}"[:2000]
        logger.error(
            "rag ingestion job failed",
            extra={
                "tenant_id": job.tenant_id,
                "job_id": job.job_id,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "error_type": type(exc).__name__,
            },
        )
        async with self._session_factory() as session:
            repository = RagIngestionJobWorkerRepository(session)
            if job.attempts < job.max_attempts:
                await repository.mark_retry(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    error_message=error_message,
                )
            else:
                await repository.mark_failed(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    error_message=error_message,
                )
            await session.commit()


async def run_forever(
    worker: RagIngestionWorker,
    stop_event: asyncio.Event,
    *,
    poll_interval_seconds: float = 1.0,
    recovery_interval_seconds: float = 30.0,
) -> None:
    """轮询任务直到收到停止信号。"""
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")
    if recovery_interval_seconds <= 0:
        raise ValueError("recovery_interval_seconds must be positive")

    recovery_elapsed = 0.0
    while not stop_event.is_set():
        if recovery_elapsed >= recovery_interval_seconds:
            await worker.recover_stale_jobs()
            recovery_elapsed = 0.0

        processed = await worker.run_once()
        if processed:
            continue

        recovery_elapsed += poll_interval_seconds
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=poll_interval_seconds,
            )
        except TimeoutError:
            pass
