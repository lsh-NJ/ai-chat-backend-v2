"""RAG 运行期组合根（Week 16 Day 1-5）。

业务服务只依赖 `AsyncRetriever` 契约；这里负责选择具体实现：
- 稀疏分支：PostgreSQL 全文检索；
- 稠密分支：pgvector + 真实本地 Embedding 模型；
- 两者通过 RRF 融合成 hybrid retriever。

Embedding 是可替换边界：测试注入 `DeterministicEmbedder`，生产注入
`SentenceTransformerEmbedder`，业务服务不变。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.embedding import Embedder
from app.rag.hybrid import AsyncHybridRetriever
from app.rag.postgres_dense import PostgresDenseRetriever
from app.rag.postgres_fts import PostgresFullTextRetriever
from app.rag.retrieval import AsyncRetriever

RagRetrieverFactory = Callable[[AsyncSession, str, Embedder], AsyncRetriever]


def create_postgres_hybrid_retriever(
    session: AsyncSession,
    tenant_id: str,
    embedder: Embedder,
) -> AsyncRetriever:
    """为一个已开启事务的 session 创建租户作用域的 hybrid retriever。"""
    if not isinstance(embedder, Embedder):
        raise TypeError("embedder must implement Embedder")
    return AsyncHybridRetriever(
        sparse_retriever=PostgresFullTextRetriever(
            session,
            tenant_id=tenant_id,
        ),
        dense_retriever=PostgresDenseRetriever(
            session,
            tenant_id=tenant_id,
            embedder=embedder.embed_query,
        ),
    )
