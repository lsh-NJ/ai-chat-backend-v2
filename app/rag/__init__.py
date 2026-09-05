"""RAG 数据管线：文档模型、解析、切分与入库。"""

from app.rag.chunking import Chunk, chunk_document
from app.rag.documents import Document, compute_content_hash
from app.rag.ingestion import (
    IngestionBatchResult,
    IngestionResult,
    IngestionStatus,
    ingest_document,
    ingest_documents,
)
from app.rag.parsers import (
    DocumentParseError,
    parse_document,
    parse_html,
    parse_markdown,
)
from app.rag.store import (
    DocumentStore,
    DuplicateContentError,
    DuplicateIdError,
    InMemoryDocumentStore,
)

__all__ = [
    "Chunk",
    "Document",
    "DocumentParseError",
    "DocumentStore",
    "DuplicateContentError",
    "DuplicateIdError",
    "InMemoryDocumentStore",
    "IngestionBatchResult",
    "IngestionResult",
    "IngestionStatus",
    "chunk_document",
    "compute_content_hash",
    "ingest_document",
    "ingest_documents",
    "parse_document",
    "parse_html",
    "parse_markdown",
]
