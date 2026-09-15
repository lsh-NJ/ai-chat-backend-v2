"""文档上传与 ingestion job 创建服务（Week 17 Day 1）。

职责边界：
- API 层只负责 HTTP 语义（认证、multipart、状态码）；
- 本服务负责校验文件、落盘、创建 pending job；
- 真正的 parse / chunk / embed 由 worker 在后续 Day 中实现。
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    RagUploadTooLargeError,
    RagUploadValidationError,
)
from app.rag.ingestion_job import IngestionJob
from app.rag.upload_storage import FileStorage, UploadStorageError
from app.repositories.rag_ingestion_job_repository import (
    RagIngestionJobRepository,
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SUPPORTED_EXTENSIONS = frozenset({".md", ".markdown", ".html", ".htm"})


class RagIngestionService:
    """创建并查询文档 ingestion job。"""

    def __init__(
        self,
        session: AsyncSession,
        storage: FileStorage,
    ) -> None:
        self._session = session
        self._storage = storage

    async def create_job(
        self,
        *,
        tenant_id: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> IngestionJob:
        """校验上传文件、保存原始内容，并创建一个 pending 任务。"""
        safe_filename = self._validate_filename(filename)
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if not data:
            raise RagUploadValidationError("上传文件不能为空")
        if len(data) > MAX_UPLOAD_BYTES:
            raise RagUploadTooLargeError(
                f"上传文件不能超过 {MAX_UPLOAD_BYTES} 字节"
            )

        job_id = f"ing-{uuid4().hex}"
        storage_key = f"{job_id}.upload"
        try:
            await self._storage.save(storage_key, data)
        except UploadStorageError as exc:
            raise RagUploadValidationError("上传文件保存失败") from exc

        job = IngestionJob(
            tenant_id=tenant_id,
            job_id=job_id,
            filename=safe_filename,
            content_type=content_type,
            content_sha256=sha256(data).hexdigest(),
            storage_key=storage_key,
            size_bytes=len(data),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        repository = RagIngestionJobRepository(
            self._session,
            tenant_id=tenant_id,
        )
        try:
            await repository.create(job)
            await self._session.commit()
        except Exception:
            # 文件已落盘但数据库失败时，避免留下永远无人引用的孤儿文件。
            await self._storage.delete(storage_key)
            raise
        return job

    @staticmethod
    def _validate_filename(filename: object) -> str:
        if not isinstance(filename, str) or not filename.strip():
            raise RagUploadValidationError("上传文件必须包含文件名")
        safe_filename = Path(filename).name
        suffix = Path(safe_filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise RagUploadValidationError(
                f"暂不支持该文件类型，仅支持: {supported}"
            )
        return safe_filename
