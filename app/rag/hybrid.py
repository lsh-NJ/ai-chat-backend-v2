"""Hybrid 检索器：组合全文与 dense 两个 AsyncRetriever（Week 15 Day 4）。

关键边界：
- 权限/租户/metadata 过滤由各个分支 retriever 在 SQL 层完成；
- Hybrid 只负责扩大候选池并做 RRF 融合；
- 同一个 AsyncSession 上不能并发执行 SQL，所以这里顺序 await；
  要并发必须为每个分支提供独立 session。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.rag.fusion import rrf_fuse
from app.rag.retrieval import (
    AsyncRetriever,
    ChunkHit,
    validate_query,
    validate_top_k,
)


def _validate_positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


class AsyncHybridRetriever:
    """把全文检索与向量检索通过 RRF 融合成 hybrid 检索器。"""

    def __init__(
        self,
        sparse_retriever: AsyncRetriever,
        dense_retriever: AsyncRetriever,
        *,
        rrf_k: int = 60,
        candidate_multiplier: int = 4,
    ) -> None:
        if not isinstance(sparse_retriever, AsyncRetriever):
            raise TypeError("sparse_retriever must implement AsyncRetriever")
        if not isinstance(dense_retriever, AsyncRetriever):
            raise TypeError("dense_retriever must implement AsyncRetriever")

        self._sparse_retriever = sparse_retriever
        self._dense_retriever = dense_retriever
        self._rrf_k = _validate_positive_int(rrf_k, "rrf_k")
        self._candidate_multiplier = _validate_positive_int(
            candidate_multiplier,
            "candidate_multiplier",
        )

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        """分别检索后做 RRF 融合，返回融合后的 top-k。"""
        validate_query(query)
        validate_top_k(top_k)
        if metadata_filter is not None and not isinstance(metadata_filter, Mapping):
            raise TypeError("metadata_filter must be a mapping")

        # candidate_multiplier 已校验为正整数，因此 candidate_k >= top_k。
        candidate_k = top_k * self._candidate_multiplier

        # 注意：这里必须顺序 await。同一个 AsyncSession 被两个协程并发使用时，
        # asyncpg 会报 “another operation is in progress”。
        sparse_hits = await self._sparse_retriever.search(
            query,
            top_k=candidate_k,
            metadata_filter=metadata_filter,
        )
        dense_hits = await self._dense_retriever.search(
            query,
            top_k=candidate_k,
            metadata_filter=metadata_filter,
        )

        return rrf_fuse(
            [sparse_hits, dense_hits],
            k=self._rrf_k,
            top_k=top_k,
        )
