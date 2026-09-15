"""Worker 专用的 ingestion job 仓库（Week 17 Day 2）。

与面向 API 的 `RagIngestionJobRepository` 不同：
- API 仓库按租户作用域查询单个任务；
- Worker 仓库需要跨租户扫描待处理任务，并用 `FOR UPDATE SKIP LOCKED`
  安全地被多个 worker 并发领取。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag import RagIngestionJob
from app.rag.ingestion_job import IngestionJob, IngestionJobStatus
from app.repositories.rag_ingestion_job_repository import row_to_ingestion_job


class RagIngestionJobWorkerRepository:
    """跨租户的 ingestion job 领取与状态更新。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_next(self) -> IngestionJob | None:
        """领取最早创建的 pending 任务，并原子地置为 running。"""
        statement = (
            select(RagIngestionJob)
            .where(RagIngestionJob.status == IngestionJobStatus.PENDING.value)
            .order_by(RagIngestionJob.created_at, RagIngestionJob.job_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        if row is None:
            return None

        row.status = IngestionJobStatus.RUNNING.value
        row.attempts += 1
        row.started_at = func.now()
        row.updated_at = func.now()
        row.error_message = None
        await self._session.flush()
        await self._session.refresh(row)
        return row_to_ingestion_job(row)

    async def mark_succeeded(self, *, tenant_id: str, job_id: str) -> None:
        row = await self._get_row(tenant_id=tenant_id, job_id=job_id)
        if row is None:
            return
        row.status = IngestionJobStatus.SUCCEEDED.value
        row.finished_at = func.now()
        row.updated_at = func.now()
        row.error_message = None
        await self._session.flush()

    async def mark_retry(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_message: str,
    ) -> None:
        row = await self._get_row(tenant_id=tenant_id, job_id=job_id)
        if row is None:
            return
        row.status = IngestionJobStatus.PENDING.value
        row.started_at = None
        row.updated_at = func.now()
        row.error_message = error_message
        await self._session.flush()

    async def mark_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_message: str,
    ) -> None:
        row = await self._get_row(tenant_id=tenant_id, job_id=job_id)
        if row is None:
            return
        row.status = IngestionJobStatus.FAILED.value
        row.finished_at = func.now()
        row.updated_at = func.now()
        row.error_message = error_message
        await self._session.flush()

    async def recover_stale_running(self, *, lease_seconds: int) -> int:
        """把超过租约仍未完成的 running 任务重新放回 pending 或标记 failed。"""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        deadline = datetime.now(UTC) - timedelta(seconds=lease_seconds)
        statement = select(RagIngestionJob).where(
            RagIngestionJob.status == IngestionJobStatus.RUNNING.value,
            RagIngestionJob.started_at < deadline,
        )
        result = await self._session.execute(statement)
        rows = result.scalars().all()

        recovered = 0
        for row in rows:
            if row.attempts >= row.max_attempts:
                row.status = IngestionJobStatus.FAILED.value
                row.finished_at = func.now()
                row.error_message = "worker lease expired; max attempts reached"
            else:
                row.status = IngestionJobStatus.PENDING.value
                row.started_at = None
                row.error_message = "worker lease expired; task requeued"
            row.updated_at = func.now()
            recovered += 1
        await self._session.flush()
        return recovered

    async def _get_row(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> RagIngestionJob | None:
        statement = select(RagIngestionJob).where(
            RagIngestionJob.tenant_id == tenant_id,
            RagIngestionJob.job_id == job_id,
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()
