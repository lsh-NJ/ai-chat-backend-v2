"""RAG 数据管线：文档模型、解析、切分与入库。"""

from app.rag.bm25 import InMemoryBM25Retriever, tokenize
from app.rag.chunking import Chunk, chunk_document
from app.rag.dense import (
    InMemoryDenseRetriever,
    cosine_similarity,
    hash_embed,
    validate_embedding,
)
from app.rag.documents import Document, compute_content_hash
from app.rag.evaluation import (
    EvalQuery,
    RetrievalReport,
    evaluate_retriever,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank_at_k,
)
from app.rag.fusion import rrf_fuse
from app.rag.hybrid import AsyncHybridRetriever
from app.rag.ingestion import (
    IngestionBatchResult,
    IngestionResult,
    IngestionStatus,
    ingest_document,
    ingest_document_async,
    ingest_documents,
    ingest_documents_async,
)
from app.rag.parsers import (
    DocumentParseError,
    parse_document,
    parse_html,
    parse_markdown,
)
from app.rag.postgres_dense import PostgresDenseRetriever
from app.rag.postgres_fts import PostgresFullTextRetriever
from app.rag.postgres_store import PostgresChunkStore, PostgresDocumentStore
from app.rag.retrieval import AsyncRetriever, ChunkHit, Retriever
from app.rag.store import (
    AsyncChunkStore,
    AsyncDocumentStore,
    DocumentNotFoundError,
    DocumentStore,
    DuplicateContentError,
    DuplicateIdError,
    InMemoryDocumentStore,
    VersionConflictError,
)

__all__ = [
    "Chunk",
    "ChunkHit",
    "Document",
    "EvalQuery",
    "InMemoryDenseRetriever",
    "RetrievalReport",
    "cosine_similarity",
    "evaluate_retriever",
    "hash_embed",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank_at_k",
    "DocumentParseError",
    "DocumentStore",
    "AsyncChunkStore",
    "AsyncDocumentStore",
    "AsyncHybridRetriever",
    "AsyncRetriever",
    "DocumentNotFoundError",
    "PostgresChunkStore",
    "PostgresDenseRetriever",
    "PostgresDocumentStore",
    "PostgresFullTextRetriever",
    "Retriever",
    "rrf_fuse",
    "tokenize",
    "DuplicateContentError",
    "DuplicateIdError",
    "InMemoryDocumentStore",
    "IngestionBatchResult",
    "IngestionResult",
    "IngestionStatus",
    "InMemoryBM25Retriever",
    "VersionConflictError",
    "chunk_document",
    "compute_content_hash",
    "validate_embedding",
    "ingest_document",
    "ingest_document_async",
    "ingest_documents",
    "ingest_documents_async",
    "parse_document",
    "parse_html",
    "parse_markdown",
]
