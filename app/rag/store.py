"""RAG 文档/chunk 存储抽象与内存实现。

通过 Protocol 定义最小存储契约：
- `DocumentStore`：同步内存实现使用的契约；
- `AsyncDocumentStore`：PostgreSQL 等异步存储使用的契约；
- `AsyncChunkStore`：chunk 落库与按文档读取的契约。

业务代码只依赖 Protocol；Day 2 的 PostgreSQL 实现放在
`app.rag.postgres_store`，上层 ingestion/retrieval 不需要知道数据库细节。
"""

from __future__ import annotations

from typing import Callable, Protocol, Sequence, runtime_checkable

from app.rag.chunking import Chunk
from app.rag.documents import Document


class DuplicateContentError(ValueError):
    """同一租户内 content_hash 已存在时抛出。"""


class DuplicateIdError(ValueError):
    """同一租户内 document id 已存在时抛出。"""


class VersionConflictError(ValueError):
    """同一租户、同一 source 的版本号被并发占用时抛出。"""


class DocumentNotFoundError(ValueError):
    """chunk 引用了不存在的 document 时抛出。"""


@runtime_checkable
class DocumentStore(Protocol):
    """同步存储层只暴露 ingestion 需要的最小能力。"""

    def contains_content_hash(self, content_hash: str) -> bool: ...

    def get_by_content_hash(self, content_hash: str) -> Document | None: ...

    def get_versions_by_source(self, source: str) -> Sequence[Document]: ...

    def save(self, document: Document) -> None: ...

    def list_documents(self) -> Sequence[Document]: ...


@runtime_checkable
class AsyncDocumentStore(Protocol):
    """异步存储层契约，方法与同步版一一对应。"""

    async def contains_content_hash(self, content_hash: str) -> bool: ...

    async def get_by_content_hash(self, content_hash: str) -> Document | None: ...

    async def get_versions_by_source(self, source: str) -> Sequence[Document]: ...

    async def save(self, document: Document) -> None: ...

    async def list_documents(self) -> Sequence[Document]: ...


@runtime_checkable
class AsyncChunkStore(Protocol):
    """chunk 存储层只暴露 ingestion/retrieval 需要的最小能力。"""

    async def save_chunks(
        self,
        document_id: str,
        chunks: Sequence[Chunk],
        *,
        embedder: Callable[[str], Sequence[float]] | None = None,
    ) -> None: ...

    async def list_chunks_by_document(self, document_id: str) -> Sequence[Chunk]: ...


class InMemoryDocumentStore:
    """内存版 DocumentStore，用于测试和最小可用基线。"""

    def __init__(self) -> None:
        self._by_hash: dict[str, Document] = {}
        self._by_id: dict[str, Document] = {}
        self._versions_by_source: dict[str, list[Document]] = {}

    def contains_content_hash(self, content_hash: str) -> bool:
        return content_hash in self._by_hash

    def get_by_content_hash(self, content_hash: str) -> Document | None:
        return self._by_hash.get(content_hash)

    def get_versions_by_source(self, source: str) -> Sequence[Document]:
        return tuple(self._versions_by_source.get(source, ()))

    def save(self, document: Document) -> None:
        if not isinstance(document, Document):
            raise TypeError("store can only save Document instances")
        if document.content_hash in self._by_hash:
            raise DuplicateContentError(
                f"content_hash already exists: {document.content_hash}"
            )
        if document.id in self._by_id:
            raise DuplicateIdError(f"document id already exists: {document.id}")

        self._by_hash[document.content_hash] = document
        self._by_id[document.id] = document
        self._versions_by_source.setdefault(document.source, []).append(document)

    def list_documents(self) -> Sequence[Document]:
        return tuple(self._by_hash.values())
