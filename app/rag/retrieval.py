"""RAG 检索层契约：检索结果模型与可替换的 Retriever。

Day 1 不实现任何打分算法，只定义“检索返回什么”和“所有检索器必须长什么样”。
后续 BM25、向量检索、pgvector 适配器都实现 `Retriever`，上层代码不绑定具体算法。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.rag.chunking import Chunk


def _validate_score(score: object) -> float:
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError("score must be a number")
    value = float(score)
    if not math.isfinite(value):
        raise ValueError("score must be finite")
    return value


@dataclass(frozen=True, slots=True)
class ChunkHit:
    """一次检索命中的最小结果单元。

    - `chunk`：命中的原始切片，包含 document_id / source / metadata / start / end；
    - `score`：相关性得分，数值越大表示越相关；
    - `rank`：1 起始的排名，1 表示最相关。
    """

    chunk: Chunk
    score: float
    rank: int

    def __post_init__(self) -> None:
        if not isinstance(self.chunk, Chunk):
            raise TypeError("chunk must be a Chunk")
        score = _validate_score(self.score)
        if not isinstance(self.rank, int) or isinstance(self.rank, bool):
            raise TypeError("rank must be an integer")
        if self.rank <= 0:
            raise ValueError("rank must be positive")

        object.__setattr__(self, "score", score)


def validate_query(query: object) -> None:
    """查询参数 fail-closed：必须是非空字符串。"""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if not query.strip():
        raise ValueError("query must not be empty")


def validate_top_k(top_k: object) -> None:
    """top_k 参数 fail-closed：必须是正整数。"""
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        raise TypeError("top_k must be an integer")
    if top_k <= 0:
        raise ValueError("top_k must be positive")


def matches_metadata(chunk: Chunk, metadata_filter: Mapping[str, Any] | None) -> bool:
    """metadata_filter 为 None 时全部通过；否则所有键值必须精确相等。"""
    if metadata_filter is None:
        return True
    return all(
        chunk.metadata.get(key) == value for key, value in metadata_filter.items()
    )


@runtime_checkable
class Retriever(Protocol):
    """RAG 检索器契约。

    输入一个非空 query，返回按相关性从高到低排序的 ChunkHit 列表。
    `metadata_filter` 是精确等值过滤，例如 `{"lang": "zh"}` 只返回
    `chunk.metadata` 中该键值完全相等的 chunk。
    """

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        """按 query 召回 top_k 个 chunk hits。"""
        ...


@runtime_checkable
class AsyncRetriever(Protocol):
    """异步 RAG 检索器契约。

    语义与 `Retriever` 完全一致，只是数据库 I/O 需要通过 await 完成。
    """

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        """按 query 异步召回 top_k 个 chunk hits。"""
        ...
