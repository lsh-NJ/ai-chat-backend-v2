"""PostgreSQL 版 RAG 文档/chunk 仓库（Week 15 Day 2）。

设计原则：
- 仓库只接收一个已开启事务的 `AsyncSession`，不主动 commit；
- 租户在构造时绑定，所有查询自动带 `tenant_id`，避免调用方漏写；
- 幂等性最终由数据库唯一约束保证，而不是只靠应用层先查后写；
- 约束冲突翻译成领域异常，让上层能区分“内容重复 / id 重复 / 版本冲突”。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag import RAG_EMBEDDING_DIMENSION, RagChunk, RagDocument
from app.rag.chunking import Chunk
from app.rag.dense import validate_embedding
from app.rag.documents import Document
from app.rag.store import (
    DocumentNotFoundError,
    DuplicateContentError,
    DuplicateIdError,
    VersionConflictError,
)

_CONTENT_HASH_CONSTRAINT = "uq_rag_documents_tenant_content_hash"
_DOCUMENT_PK_CONSTRAINT = "pk_rag_documents"
_VERSION_CONSTRAINT = "uq_rag_documents_tenant_source_version"
_CHUNK_DOCUMENT_FK_CONSTRAINT = "fk_rag_chunks_tenant_document_rag_documents"


def row_to_chunk(row: RagChunk) -> Chunk:
    """把 ORM chunk 行转换成领域 Chunk，供仓库和检索器共用。"""
    return Chunk(
        id=row.chunk_id,
        document_id=row.document_id,
        source=row.source,
        content=row.content,
        metadata=dict(row.chunk_metadata),
        start=row.start,
        end=row.end,
    )


def _constraint_name(exc: IntegrityError) -> str:
    """从 asyncpg 的异常链里提取约束名，用于精确翻译领域错误。

    SQLAlchemy 的 asyncpg 方言会把底层 asyncpg 异常包在
    ``AsyncAdapt_asyncpg_dbapi.*`` 里，``constraint_name`` 往往在更内层的
    ``__cause__`` / ``__context__``，所以必须沿异常链查找，不能只看 ``exc.orig``。
    """
    error: BaseException | None = exc.orig
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        name = getattr(error, "constraint_name", None)
        if isinstance(name, str) and name:
            return name
        error = error.__cause__ or error.__context__
    return ""


def _document_version(document: Document) -> int:
    """从 Document.metadata 读取版本号；缺省视为 1，非法值 fail-closed。"""
    raw_version = document.metadata.get("version", 1)
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("document metadata version must be an integer") from exc
    if version <= 0:
        raise ValueError("document metadata version must be positive")
    return version


class PostgresDocumentStore:
    """租户作用域内的 PostgreSQL 文档仓库。"""

    def __init__(self, session: AsyncSession, *, tenant_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        self._session = session
        self._tenant_id = tenant_id

    def _to_document(self, row: RagDocument) -> Document:
        metadata: dict[str, Any] = dict(row.doc_metadata)
        metadata["version"] = row.version
        return Document(
            id=row.document_id,
            source=row.source,
            content=row.content,
            metadata=metadata,
        )

    def _to_row(self, document: Document) -> RagDocument:
        return RagDocument(
            tenant_id=self._tenant_id,
            document_id=document.id,
            source=document.source,
            version=_document_version(document),
            content=document.content,
            content_hash=document.content_hash,
            doc_metadata=dict(document.metadata),
        )

    def _translate_integrity_error(self, exc: IntegrityError) -> ValueError:
        name = _constraint_name(exc)
        if name == _CONTENT_HASH_CONSTRAINT:
            return DuplicateContentError(
                f"content_hash already exists in tenant: {self._tenant_id}"
            )
        if name == _DOCUMENT_PK_CONSTRAINT:
            return DuplicateIdError(
                f"document id already exists in tenant: {self._tenant_id}"
            )
        if name == _VERSION_CONSTRAINT:
            return VersionConflictError(
                "document version conflict for the same tenant/source"
            )
        return ValueError(f"database rejected document write: {name or exc}")

    async def contains_content_hash(self, content_hash: str) -> bool:
        statement = (
            select(RagDocument.document_id)
            .where(
                RagDocument.tenant_id == self._tenant_id,
                RagDocument.content_hash == content_hash,
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def get_by_content_hash(self, content_hash: str) -> Document | None:
        statement = select(RagDocument).where(
            RagDocument.tenant_id == self._tenant_id,
            RagDocument.content_hash == content_hash,
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return None if row is None else self._to_document(row)

    async def get_versions_by_source(self, source: str) -> Sequence[Document]:
        statement = (
            select(RagDocument)
            .where(
                RagDocument.tenant_id == self._tenant_id,
                RagDocument.source == source,
            )
            .order_by(RagDocument.version)
        )
        result = await self._session.execute(statement)
        return tuple(self._to_document(row) for row in result.scalars())

    async def list_documents(self) -> Sequence[Document]:
        statement = (
            select(RagDocument)
            .where(RagDocument.tenant_id == self._tenant_id)
            .order_by(RagDocument.source, RagDocument.version)
        )
        result = await self._session.execute(statement)
        return tuple(self._to_document(row) for row in result.scalars())

    async def save(self, document: Document) -> None:
        if not isinstance(document, Document):
            raise TypeError("store can only save Document instances")
        try:
            # SAVEPOINT：单条文档失败只回滚这一条，不误伤同一事务里已写入的其他文档。
            async with self._session.begin_nested():
                self._session.add(self._to_row(document))
                await self._session.flush()
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc


class PostgresChunkStore:
    """租户作用域内的 PostgreSQL chunk 仓库。"""

    def __init__(self, session: AsyncSession, *, tenant_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        self._session = session
        self._tenant_id = tenant_id

    def _to_chunk(self, row: RagChunk) -> Chunk:
        return row_to_chunk(row)

    async def save_chunks(
        self,
        document_id: str,
        chunks: Sequence[Chunk],
        *,
        embedder: Callable[[str], Sequence[float]] | None = None,
    ) -> None:
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("document_id must be a non-empty string")
        if not chunks:
            return
        if embedder is not None and not callable(embedder):
            raise TypeError("embedder must be callable")

        values: list[dict[str, Any]] = []
        for chunk in chunks:
            if not isinstance(chunk, Chunk):
                raise TypeError("chunks must contain Chunk instances")
            if chunk.document_id != document_id:
                raise ValueError(
                    "every chunk.document_id must match the target document_id"
                )
            embedding = (
                None
                if embedder is None
                else validate_embedding(
                    embedder(chunk.content),
                    dimensions=RAG_EMBEDDING_DIMENSION,
                )
            )
            values.append(
                {
                    "tenant_id": self._tenant_id,
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "source": chunk.source,
                    "content": chunk.content,
                    "chunk_metadata": dict(chunk.metadata),
                    "start": chunk.start,
                    "end": chunk.end,
                    "embedding": embedding,
                }
            )

        insert_statement = pg_insert(RagChunk).values(values)
        if embedder is None:
            statement = insert_statement.on_conflict_do_nothing(
                index_elements=["tenant_id", "chunk_id"],
            )
        else:
            # 已有 embedding 保留；NULL 的旧行回填，保证“先落 chunk 再补向量”可重跑。
            statement = insert_statement.on_conflict_do_update(
                index_elements=["tenant_id", "chunk_id"],
                set_={"embedding": insert_statement.excluded.embedding},
                where=RagChunk.embedding.is_(None),
            )

        try:
            # SAVEPOINT：一批 chunk 失败只回滚这一批，不误伤同事务里的其他写入。
            async with self._session.begin_nested():
                await self._session.execute(statement)
        except IntegrityError as exc:
            if _constraint_name(exc) == _CHUNK_DOCUMENT_FK_CONSTRAINT:
                raise DocumentNotFoundError(
                    f"document not found in tenant: {document_id}"
                ) from exc
            raise ValueError(
                f"database rejected chunk write: {_constraint_name(exc) or exc}"
            ) from exc

    async def list_chunks_by_document(self, document_id: str) -> Sequence[Chunk]:
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("document_id must be a non-empty string")
        statement = (
            select(RagChunk)
            .where(
                RagChunk.tenant_id == self._tenant_id,
                RagChunk.document_id == document_id,
            )
            .order_by(RagChunk.start, RagChunk.chunk_id)
        )
        result = await self._session.execute(statement)
        return tuple(self._to_chunk(row) for row in result.scalars())
