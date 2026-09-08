"""Week 14 Day 3：向量检索与余弦相似度。

- `cosine_similarity`：向量方向相似度；
- `hash_embed`：确定性的玩具 embedding（真实语义模型在 Week 15 接入）；
- `InMemoryDenseRetriever`：接收任意 embedder 的稠密检索器。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.rag.bm25 import tokenize
from app.rag.chunking import Chunk
from app.rag.retrieval import (
    ChunkHit,
    matches_metadata,
    validate_query,
    validate_top_k,
)


def _vector_from_sequence(values: object, name: str = "vector") -> list[float]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{name} must be a non-empty sequence of numbers")
    if len(values) == 0:
        raise ValueError(f"{name} must not be empty")

    result: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} values must be numbers")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{name} values must be finite")
        result.append(number)
    return result


def _unit_vector(values: Sequence[float]) -> list[float]:
    vector = _vector_from_sequence(values)
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if norm == 0.0:
        raise ValueError("cannot normalize a zero vector")
    return [value / norm for value in vector]


def cosine_similarity(
    vector_a: Sequence[float],
    vector_b: Sequence[float],
) -> float:
    """计算两个非零向量的余弦相似度，范围通常为 [-1, 1]。"""
    a = _vector_from_sequence(vector_a, "vector_a")
    b = _vector_from_sequence(vector_b, "vector_b")
    if len(a) != len(b):
        raise ValueError("vectors must have the same dimension")

    return math.fsum(
        x * y
        for x, y in zip(_unit_vector(a), _unit_vector(b), strict=True)
    )


def hash_embed(text: str, *, dimensions: int = 128) -> list[float]:
    """确定性玩具 embedding：把每个词项稳定映射到固定维度向量。

    这不是真实语义 Embedding，只用于学习和测试检索机制。
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise TypeError("dimensions must be an integer")
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")

    vector = [0.0] * dimensions
    for term in tokenize(text):
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], byteorder="big") % dimensions
        vector[index] += 1.0
    return vector


class InMemoryDenseRetriever:
    """基于余弦相似度的内存 dense retriever。

    `embedder` 是把文本变成固定维度向量的函数。真实 Embedding 接入时，
    只需替换 embedder，检索器本身不变。
    """

    def __init__(
        self,
        chunks: Sequence[Chunk],
        embedder: Callable[[str], Sequence[float]],
    ) -> None:
        if not callable(embedder):
            raise TypeError("embedder must be callable")

        self._chunks = list(chunks)
        if any(not isinstance(chunk, Chunk) for chunk in self._chunks):
            raise TypeError("chunks must contain Chunk values")

        self._embedder = embedder
        self._vectors: list[list[float]] = []
        self._dimension: int | None = None

        for chunk in self._chunks:
            vector = _unit_vector(self._embedder(chunk.content))
            if self._dimension is None:
                self._dimension = len(vector)
            elif len(vector) != self._dimension:
                raise ValueError(
                    "embedder returned inconsistent vector dimensions"
                )
            self._vectors.append(vector)

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        """按余弦相似度返回 top-k ChunkHit。"""
        validate_query(query)
        validate_top_k(top_k)
        if metadata_filter is not None and not isinstance(metadata_filter, Mapping):
            raise TypeError("metadata_filter must be a mapping")

        if not self._chunks or self._dimension is None:
            return []

        query_vector = _unit_vector(self._embedder(query))
        if len(query_vector) != self._dimension:
            raise ValueError("query embedding dimension does not match index")

        scores: dict[int, float] = {}
        for index, chunk in enumerate(self._chunks):
            if not matches_metadata(chunk, metadata_filter):
                continue
            scores[index] = math.fsum(
                a * b
                for a, b in zip(
                    query_vector,
                    self._vectors[index],
                    strict=True,
                )
            )

        ranked = sorted(
            scores.items(),
            key=lambda item: (-item[1], self._chunks[item[0]].id),
        )

        return [
            ChunkHit(
                chunk=self._chunks[index],
                score=score,
                rank=rank,
            )
            for rank, (index, score) in enumerate(ranked[:top_k], start=1)
        ]
