"""RAG 幂等导入与版本管理。

策略（当前基线）：
- content_hash 在租户内唯一：相同正文无论来自哪个 source 都只保留一份；
- 同一 source 内容变化时生成新版本；
- 批量导入可安全重跑：已导入的自动跳过，等价于“可恢复”；
- 同步版用于内存 store；异步版用于 PostgreSQL 等异步仓库。

版本号规则：同一 source 的下一个版本 = 已有最大版本 + 1。
数据库层再用 `(tenant_id, source, version)` 唯一约束兜底并发写入。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from app.rag.documents import Document
from app.rag.store import (
    AsyncDocumentStore,
    DocumentStore,
    DuplicateContentError,
    VersionConflictError,
)

_INGESTION_RETRY_ATTEMPTS = 3


class IngestionStatus(str, Enum):
    CREATED = "created"
    SKIPPED = "skipped"
    UPDATED = "updated"


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """单篇文档的导入结果。"""

    document: Document
    status: IngestionStatus
    version: int


@dataclass(frozen=True, slots=True)
class IngestionBatchResult:
    """一批文档的导入汇总。"""

    results: Sequence[IngestionResult]
    created: int
    skipped: int
    updated: int


def _document_version(document: Document) -> int:
    """读取 Document.metadata 中的版本号，缺省为 1。"""
    try:
        version = int(document.metadata.get("version", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("document metadata version must be an integer") from exc
    if version <= 0:
        raise ValueError("document metadata version must be positive")
    return version


def _next_version(previous_versions: Sequence[Document]) -> int:
    """同一 source 的下一个版本 = 已有最大版本 + 1。"""
    if not previous_versions:
        return 1
    return max(_document_version(document) for document in previous_versions) + 1


def _with_version(document: Document, version: int) -> Document:
    metadata = dict(document.metadata)
    metadata["version"] = version
    metadata["base_document_id"] = document.id
    # 存储层要求 id 唯一；追加版本号让同源内容变化时也有独立 id。
    versioned_id = f"{document.id}::v{version}"
    return Document(
        id=versioned_id,
        source=document.source,
        content=document.content,
        metadata=metadata,
    )


def ingest_document(
    store: DocumentStore,
    document: Document,
) -> IngestionResult:
    """导入单篇文档：新内容写入、重复内容跳过、同 source 新内容生成新版本。"""
    if not isinstance(document, Document):
        raise TypeError("document must be a Document")

    existing = store.get_by_content_hash(document.content_hash)
    if existing is not None:
        return IngestionResult(
            document=existing,
            status=IngestionStatus.SKIPPED,
            version=_document_version(existing),
        )

    previous_versions = store.get_versions_by_source(document.source)
    status = (
        IngestionStatus.UPDATED
        if previous_versions
        else IngestionStatus.CREATED
    )
    version = _next_version(previous_versions)
    versioned_document = _with_version(document, version)
    store.save(versioned_document)
    return IngestionResult(
        document=versioned_document,
        status=status,
        version=version,
    )


async def ingest_document_async(
    store: AsyncDocumentStore,
    document: Document,
) -> IngestionResult:
    """异步导入单篇文档；数据库唯一约束兜底并发写入。"""
    if not isinstance(document, Document):
        raise TypeError("document must be a Document")

    for _ in range(_INGESTION_RETRY_ATTEMPTS):
        existing = await store.get_by_content_hash(document.content_hash)
        if existing is not None:
            return IngestionResult(
                document=existing,
                status=IngestionStatus.SKIPPED,
                version=_document_version(existing),
            )

        previous_versions = await store.get_versions_by_source(document.source)
        status = (
            IngestionStatus.UPDATED
            if previous_versions
            else IngestionStatus.CREATED
        )
        version = _next_version(previous_versions)
        versioned_document = _with_version(document, version)

        try:
            await store.save(versioned_document)
        except DuplicateContentError:
            # 并发写入同一内容：重新查询后返回 SKIPPED。
            continue
        except VersionConflictError:
            # 并发写入同 source 的不同内容：重新计算版本号。
            continue

        return IngestionResult(
            document=versioned_document,
            status=status,
            version=version,
        )

    raise RuntimeError("ingestion retry limit exceeded")


def ingest_documents(
    store: DocumentStore,
    documents: Iterable[Document],
) -> IngestionBatchResult:
    """批量导入；重复执行同一批次是安全的（幂等）。"""
    results: list[IngestionResult] = []
    created = 0
    skipped = 0
    updated = 0

    for document in documents:
        result = ingest_document(store, document)
        results.append(result)
        if result.status == IngestionStatus.CREATED:
            created += 1
        elif result.status == IngestionStatus.SKIPPED:
            skipped += 1
        elif result.status == IngestionStatus.UPDATED:
            updated += 1

    return IngestionBatchResult(
        results=tuple(results),
        created=created,
        skipped=skipped,
        updated=updated,
    )


async def ingest_documents_async(
    store: AsyncDocumentStore,
    documents: Iterable[Document],
) -> IngestionBatchResult:
    """异步批量导入；重复执行同一批次是安全的（幂等）。"""
    results: list[IngestionResult] = []
    created = 0
    skipped = 0
    updated = 0

    for document in documents:
        result = await ingest_document_async(store, document)
        results.append(result)
        if result.status == IngestionStatus.CREATED:
            created += 1
        elif result.status == IngestionStatus.SKIPPED:
            skipped += 1
        elif result.status == IngestionStatus.UPDATED:
            updated += 1

    return IngestionBatchResult(
        results=tuple(results),
        created=created,
        skipped=skipped,
        updated=updated,
    )
