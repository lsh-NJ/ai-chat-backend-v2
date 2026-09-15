"""Ingestion job 的 PostgreSQL 仓库（Week 17 Day 1）。

仓库在构造时绑定 tenant_id，所有查询自动带租户条件，避免调用方漏写
`WHERE tenant_id = ...` 导致跨租户读取。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag import RagIngestionJob
from app.rag.ingestion_job import IngestionJob, IngestionJobStatus


def row_to_ingestion_job(row: RagIngestionJob) -> IngestionJob:
    return IngestionJob(
        tenant_id=row.tenant_id,
        job_id=row.job_id,
        filename=row.filename,
        content_type=row.content_type,
        content_sha256=row.content_sha256,
        storage_key=row.storage_key,
        size_bytes=row.size_bytes,
        status=IngestionJobStatus(row.status),
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def ingestion_job_to_row(job: IngestionJob) -> RagIngestionJob:
    return RagIngestionJob(
        tenant_id=job.tenant_id,
        job_id=job.job_id,
        filename=job.filename,
        content_type=job.content_type,
        content_sha256=job.content_sha256,
        storage_key=job.storage_key,
        size_bytes=job.size_bytes,
        status=job.status.value,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        error_message=job.error_message,
    )


class RagIngestionJobRepository:
    """租户作用域内的 ingestion job 仓库。"""

    def __init__(self, session: AsyncSession, *, tenant_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        self._session = session
        self._tenant_id = tenant_id

    async def create(self, job: IngestionJob) -> None:
        if not isinstance(job, IngestionJob):
            raise TypeError("job must be an IngestionJob")
        if job.tenant_id != self._tenant_id:
            raise ValueError("job.tenant_id does not match repository tenant")
        self._session.add(ingestion_job_to_row(job))
        await self._session.flush()

    async def get(self, job_id: str) -> IngestionJob | None:
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        statement = select(RagIngestionJob).where(
            RagIngestionJob.tenant_id == self._tenant_id,
            RagIngestionJob.job_id == job_id,
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return None if row is None else row_to_ingestion_job(row)
