"""PostgreSQL 全文检索器（Week 15 Day 4 / Week 16 Day 6）。

说明：
- 这里实现的是 PostgreSQL 内置全文检索，排序函数是 `ts_rank_cd`；
- `ts_rank_cd` 不是 BM25，它是 cover density 排序；本周把它作为
  hybrid 的“稀疏/全文”基线分支，Week14 的 `InMemoryBM25Retriever`
  仍是真正的 BM25 参考实现；
- PostgreSQL `simple` 配置不会自动切分中文。这里在 SQL 里把非 ASCII
  字符逐个用空格隔开后交给 `simple` parser，得到一个无扩展依赖的
  中文字符级 FTS 基线；查询侧用 `OR` 组合字符词项，避免“查询里出现
  一个资料中没有的字符就整条不匹配”。生产环境仍建议换 zhparser/pg_jieba。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag import RagChunk
from app.rag.bm25 import tokenize
from app.rag.postgres_store import row_to_chunk
from app.rag.retrieval import (
    ChunkHit,
    validate_query,
    validate_top_k,
)

# 显式写 regconfig 字面量，让查询表达式尽量与 GIN 表达式索引一致。
_TS_CONFIG = literal_column("'simple'::regconfig")
_CJK_GAP_PATTERN = "([^[:ascii:]])"
_CJK_GAP_REPLACEMENT = " \\1 "


def _segment_cjk(expression: Any) -> Any:
    """把非 ASCII 字符逐字隔开，让 simple parser 能切出中文字符。

    例如：
      "退款怎么申请" -> "退 款 怎 么 申 请"
    """
    return func.regexp_replace(
        expression,
        _CJK_GAP_PATTERN,
        _CJK_GAP_REPLACEMENT,
        "g",
    )


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

        search_vector = func.to_tsvector(
            _TS_CONFIG,
            _segment_cjk(RagChunk.content),
        )
        if any(ord(char) > 127 for char in query):
            # 中文字符级 FTS：用 OR 组合单字，避免“查询里有一个资料没有的
            # 字符就整条不匹配”；ts_rank_cd 仍然会奖励命中更多字符的文档。
            query_terms = tokenize(query)
            if not query_terms:
                return ()
            ts_query = func.to_tsquery(
                _TS_CONFIG,
                " | ".join(query_terms),
            )
        else:
            # 纯英文/数字保留 PostgreSQL 的 websearch AND 语义。
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
