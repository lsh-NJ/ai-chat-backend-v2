"""pgvector 稠密检索器（Week 15 Day 3）。

检索语义：
1. 用同一个 embedder 把 query 变成向量；
2. 在 SQL 的 WHERE 里先做 tenant / metadata 过滤；
3. 用 pgvector `<=>` 余弦距离排序；
4. LIMIT 截断 top-k；
5. 把“余弦距离”转换成 Week14 约定的“相似度得分（越大越相关）”。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag import RAG_EMBEDDING_DIMENSION, RagChunk
from app.rag.dense import validate_embedding
from app.rag.postgres_store import row_to_chunk
from app.rag.retrieval import (
    ChunkHit,
    validate_query,
    validate_top_k,
)


class PostgresDenseRetriever:
    """基于 pgvector 的异步 dense retriever。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        embedder: Callable[[str], Sequence[float]],
        dimension: int = RAG_EMBEDDING_DIMENSION,
    ) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if not callable(embedder):
            raise TypeError("embedder must be callable")
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise TypeError("dimension must be an integer")
        if dimension <= 0:
            raise ValueError("dimension must be positive")

        self._session = session
        self._tenant_id = tenant_id
        self._embedder = embedder
        self._dimension = dimension

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        """按查询向量余弦距离返回 top-k ChunkHit。"""
        validate_query(query)
        validate_top_k(top_k)
        if metadata_filter is not None and not isinstance(metadata_filter, Mapping):
            raise TypeError("metadata_filter must be a mapping")

        query_vector = validate_embedding(
            self._embedder(query),
            dimensions=self._dimension,
            name="query embedding",
        )

        distance = RagChunk.embedding.cosine_distance(query_vector)
        statement = select(RagChunk, distance).where(
            RagChunk.tenant_id == self._tenant_id,
            RagChunk.embedding.is_not(None),
        )
        if metadata_filter is not None:
            statement = statement.where(
                RagChunk.chunk_metadata.contains(metadata_filter)
            )
        statement = statement.order_by(
            distance,
            RagChunk.chunk_id,
        ).limit(top_k)

        result = await self._session.execute(statement)
        hits: list[ChunkHit] = []
        for rank, row in enumerate(result.all(), start=1):
            chunk_row, distance_value = row
            hits.append(
                ChunkHit(
                    chunk=row_to_chunk(chunk_row),
                    score=1.0 - float(distance_value),
                    rank=rank,
                )
            )
        return tuple(hits)
