"""PostgreSQL 全文检索器（Week 15 Day 4）。

说明：
- 这里实现的是 PostgreSQL 内置全文检索，排序函数是 `ts_rank_cd`；
- `ts_rank_cd` 不是 BM25，它是 cover density 排序；本周把它作为
  hybrid 的“稀疏/全文”基线分支，Week14 的 `InMemoryBM25Retriever`
  仍是真正的 BM25 参考实现；
- 当前使用 `simple` 配置，不做中文分词；中文 BM25 需要 zhparser/pg_jieba。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag import RagChunk
from app.rag.postgres_store import row_to_chunk
from app.rag.retrieval import (
    ChunkHit,
    validate_query,
    validate_top_k,
)

# 显式写 regconfig 字面量，让查询表达式尽量与 GIN 表达式索引一致：
#   to_tsvector('simple'::regconfig, content)
_TS_CONFIG = literal_column("'simple'::regconfig")


class PostgresFullTextRetriever:
    """基于 PostgreSQL `tsvector` / `ts_rank_cd` 的异步全文检索器。"""

    def __init__(self, session: AsyncSession, *, tenant_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        self._session = session
        self._tenant_id = tenant_id

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        """按全文相关度返回 top-k ChunkHit。"""
        validate_query(query)
        validate_top_k(top_k)
        if metadata_filter is not None and not isinstance(metadata_filter, Mapping):
            raise TypeError("metadata_filter must be a mapping")

        search_vector = func.to_tsvector(_TS_CONFIG, RagChunk.content)
        ts_query = func.websearch_to_tsquery(_TS_CONFIG, query)
        rank = func.ts_rank_cd(search_vector, ts_query).label("rank")

        statement = select(RagChunk, rank).where(
            RagChunk.tenant_id == self._tenant_id,
            search_vector.op("@@")(ts_query),
        )
        if metadata_filter is not None:
            statement = statement.where(
                RagChunk.chunk_metadata.contains(metadata_filter)
            )
        statement = statement.order_by(
            rank.desc(),
            RagChunk.chunk_id,
        ).limit(top_k)

        result = await self._session.execute(statement)
        hits: list[ChunkHit] = []
        for hit_rank, row in enumerate(result.all(), start=1):
            chunk_row, rank_value = row
            hits.append(
                ChunkHit(
                    chunk=row_to_chunk(chunk_row),
                    score=float(rank_value),
                    rank=hit_rank,
                )
            )
        return tuple(hits)
