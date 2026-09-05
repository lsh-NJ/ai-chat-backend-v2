"""RAG 幂等导入与版本管理。

策略（当前基线）：
- content_hash 全局唯一：相同正文无论来自哪个 source 都只保留一份；
- 同一 source 内容变化时生成新版本；
- 批量导入可安全重跑：已导入的自动跳过，等价于“可恢复”。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from app.rag.documents import Document
from app.rag.store import DocumentStore


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
        # 已存在的版本号取自该文档元数据；找不到时保守视为 1
        version = int(existing.metadata.get("version", 1))
        return IngestionResult(
            document=existing,
            status=IngestionStatus.SKIPPED,
            version=version,
        )

    previous_versions = store.get_versions_by_source(document.source)
    if previous_versions:
        status = IngestionStatus.UPDATED
        version = len(previous_versions) + 1
    else:
        status = IngestionStatus.CREATED
        version = 1

    versioned_document = _with_version(document, version)
    store.save(versioned_document)
    return IngestionResult(
        document=versioned_document,
        status=status,
        version=version,
    )


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
