"""Week 17 Day 2：RAG ingestion worker 的真实数据库验收。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionFactory
from app.models.rag import RagChunk, RagDocument, RagIngestionJob
from app.rag.ingestion_job import IngestionJob, IngestionJobStatus
from app.rag.ingestion_processor import PostgresIngestionProcessor
from app.repositories.rag_ingestion_job_repository import (
    RagIngestionJobRepository,
)
from app.repositories.rag_ingestion_job_worker_repository import (
    RagIngestionJobWorkerRepository,
)
from app.services.rag_ingestion_service import RagIngestionService
from app.tests.fakes import DeterministicEmbedder
from app.workers.rag_ingestion_worker import RagIngestionWorker


async def _create_job(
    storage,
    *,
    tenant_id: str = "tenant-a",
    filename: str = "refund.md",
    content: bytes = b"# Refund\n\n7 days return policy.",
) -> IngestionJob:
    async with AsyncSessionFactory() as session:
        return await RagIngestionService(session, storage).create_job(
            tenant_id=tenant_id,
            filename=filename,
            content_type="text/markdown",
            data=content,
        )


def _build_worker(storage, *, processor=None, lease_seconds: int = 300):
    return RagIngestionWorker(
        processor=processor
        or PostgresIngestionProcessor(
            storage=storage,
            embedder=DeterministicEmbedder(),
            chunk_size=100,
            overlap=10,
        ),
        lease_seconds=lease_seconds,
    )


async def _get_job(tenant_id: str, job_id: str) -> IngestionJob | None:
    async with AsyncSessionFactory() as session:
        return await RagIngestionJobRepository(
            session,
            tenant_id=tenant_id,
        ).get(job_id)


async def _count_rows(model, tenant_id: str) -> int:
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(model).where(model.tenant_id == tenant_id)
        )
        return len(result.scalars().all())


async def test_worker_processes_uploaded_markdown(
    fresh_schema,
    rag_upload_storage,
) -> None:
    job = await _create_job(rag_upload_storage)
    worker = _build_worker(rag_upload_storage)

    assert await worker.run_once() is True

    stored = await _get_job(job.tenant_id, job.job_id)
    assert stored is not None
    assert stored.status is IngestionJobStatus.SUCCEEDED
    assert stored.attempts == 1
    assert stored.finished_at is not None
    assert await _count_rows(RagDocument, job.tenant_id) == 1
    assert await _count_rows(RagChunk, job.tenant_id) >= 1

    assert await worker.run_once() is False


async def test_worker_is_idempotent_for_same_content(
    fresh_schema,
    rag_upload_storage,
) -> None:
    content = b"# Refund\n\nSame content should not create duplicate documents."
    first = await _create_job(rag_upload_storage, content=content)
    second = await _create_job(rag_upload_storage, content=content)
    worker = _build_worker(rag_upload_storage)

    assert await worker.run_once() is True
    assert await worker.run_once() is True

    assert (await _get_job(first.tenant_id, first.job_id)).status is (
        IngestionJobStatus.SUCCEEDED
    )
    assert (await _get_job(second.tenant_id, second.job_id)).status is (
        IngestionJobStatus.SUCCEEDED
    )
    assert await _count_rows(RagDocument, first.tenant_id) == 1
    assert await _count_rows(RagChunk, first.tenant_id) >= 1


class _AlwaysFailingProcessor:
    async def process(self, session: AsyncSession, job: IngestionJob) -> None:
        raise RuntimeError("boom")


async def test_worker_retries_until_max_attempts(
    fresh_schema,
    rag_upload_storage,
) -> None:
    job = await _create_job(rag_upload_storage)
    worker = _build_worker(
        rag_upload_storage,
        processor=_AlwaysFailingProcessor(),
    )

    assert await worker.run_once() is True
    stored = await _get_job(job.tenant_id, job.job_id)
    assert stored is not None
    assert stored.status is IngestionJobStatus.PENDING
    assert stored.attempts == 1

    assert await worker.run_once() is True
    assert await worker.run_once() is True

    stored = await _get_job(job.tenant_id, job.job_id)
    assert stored is not None
    assert stored.status is IngestionJobStatus.FAILED
    assert stored.attempts == 3
    assert stored.error_message is not None
    assert "boom" in stored.error_message


async def test_worker_recovers_stale_running_job(
    fresh_schema,
    rag_upload_storage,
) -> None:
    job = await _create_job(rag_upload_storage)
    async with AsyncSessionFactory() as session:
        claimed = await RagIngestionJobWorkerRepository(session).claim_next()
        assert claimed is not None
        await session.commit()

    async with AsyncSessionFactory() as session:
        row = await session.get(
            RagIngestionJob,
            {"tenant_id": job.tenant_id, "job_id": job.job_id},
        )
        assert row is not None
        row.started_at = datetime.now(UTC) - timedelta(seconds=10_000)
        await session.commit()

    worker = _build_worker(rag_upload_storage, lease_seconds=300)
    assert await worker.recover_stale_jobs() == 1

    stored = await _get_job(job.tenant_id, job.job_id)
    assert stored is not None
    assert stored.status is IngestionJobStatus.PENDING
    assert stored.started_at is None
