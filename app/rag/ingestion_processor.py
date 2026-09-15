"""把上传文件处理成可检索 chunk 的 processor（Week 17 Day 2）。

Worker 只负责任务生命周期、重试和状态更新；本模块负责真正的数据转换：
读取原始文件 → UTF-8 解码 → 解析 → 切分 → embedding → 写入 PostgreSQL。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RagIngestionProcessingError
from app.rag.chunking import chunk_document
from app.rag.documents import Document
from app.rag.embedding import Embedder
from app.rag.ingestion import ingest_document_async
from app.rag.ingestion_job import IngestionJob
from app.rag.parsers import DocumentFormat, DocumentParseError, parse_document
from app.rag.postgres_store import PostgresChunkStore, PostgresDocumentStore
from app.rag.upload_storage import FileStorage, UploadStorageError

_FORMAT_BY_SUFFIX: dict[str, DocumentFormat] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
}


@runtime_checkable
class IngestionProcessor(Protocol):
    """把一份 ingestion job 的原始文件变成可检索数据。"""

    async def process(self, session: AsyncSession, job: IngestionJob) -> None: ...


class PostgresIngestionProcessor:
    """当前生产路径：解析 + 切分 + BGE embedding + PostgreSQL 写入。"""

    def __init__(
        self,
        *,
        storage: FileStorage,
        embedder: Embedder,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> None:
        if not isinstance(chunk_size, int) or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        if not isinstance(overlap, int) or overlap < 0:
            raise ValueError("overlap must be a non-negative integer")
        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        self._storage = storage
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._overlap = overlap

    async def process(self, session: AsyncSession, job: IngestionJob) -> None:
        document = await self._parse_document(job)

        ingestion_result = await ingest_document_async(
            PostgresDocumentStore(session, tenant_id=job.tenant_id),
            document,
        )
        # 文档先提交，chunk 再写；即使第二阶段失败，重试也能通过幂等写入恢复。
        await session.commit()

        chunks = chunk_document(
            ingestion_result.document,
            chunk_size=self._chunk_size,
            overlap=self._overlap,
        )
        await PostgresChunkStore(
            session,
            tenant_id=job.tenant_id,
        ).save_chunks(
            ingestion_result.document.id,
            chunks,
            embedder=self._embedder.embed_query,
        )
        await session.commit()

    async def _parse_document(self, job: IngestionJob) -> Document:
        try:
            raw = await self._storage.read(job.storage_key)
        except UploadStorageError as exc:
            raise RagIngestionProcessingError(
                "读取上传文件失败"
            ) from exc

        try:
            raw_text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RagIngestionProcessingError(
                "上传文件不是有效的 UTF-8 文本"
            ) from exc

        suffix = Path(job.filename).suffix.lower()
        format_name = _FORMAT_BY_SUFFIX.get(suffix)
        if format_name is None:
            raise RagIngestionProcessingError("不支持的文件类型")

        try:
            return parse_document(
                raw_text,
                source=job.filename,
                format_name=format_name,
                metadata={"filename": job.filename, "upload_job_id": job.job_id},
            )
        except DocumentParseError as exc:
            raise RagIngestionProcessingError(str(exc)) from exc
