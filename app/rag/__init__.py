"""RAG 数据管线：文档模型、解析、切分与入库。"""

from app.rag.bm25 import InMemoryBM25Retriever, tokenize
from app.rag.chunking import Chunk, chunk_document
from app.rag.dense import (
    InMemoryDenseRetriever,
    cosine_similarity,
    hash_embed,
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
from app.rag.retrieval import ChunkHit, Retriever
from app.rag.store import (
    DocumentStore,
    DuplicateContentError,
    DuplicateIdError,
    InMemoryDocumentStore,
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
    "Retriever",
    "tokenize",
    "DuplicateContentError",
    "DuplicateIdError",
    "InMemoryDocumentStore",
    "IngestionBatchResult",
    "IngestionResult",
    "IngestionStatus",
    "InMemoryBM25Retriever",
    "chunk_document",
    "compute_content_hash",
    "ingest_document",
    "ingest_documents",
    "parse_document",
    "parse_html",
    "parse_markdown",
]
