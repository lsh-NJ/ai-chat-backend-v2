"""Week 14 Day 2：倒排索引与 BM25 稀疏检索基线。

实现是纯 Python、确定性的，不依赖外部搜索引擎。
- `tokenize`：英文按字母/数字词，中文按汉字；
- `InMemoryBM25Retriever`：构建倒排索引后按 BM25 打分返回 ChunkHit。
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.rag.chunking import Chunk
from app.rag.retrieval import ChunkHit, validate_query, validate_top_k

_LATIN_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_START = 0x4E00
_CJK_END = 0x9FFF


def tokenize(text: str) -> list[str]:
    """把文本切成可检索词项。

    - 英文/数字转小写后按连续字母数字切分；
    - 中文没有空格，按单个汉字切分；
    - 标点和其他符号不作为检索词项。
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    lowered = text.lower()
    terms = _LATIN_TOKEN_RE.findall(lowered)
    terms.extend(char for char in lowered if _CJK_START <= ord(char) <= _CJK_END)
    return terms


def _validate_float_param(value: object, name: str, *, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return result


def _matches(chunk: Chunk, metadata_filter: Mapping[str, Any] | None) -> bool:
    if metadata_filter is None:
        return True
    return all(
        chunk.metadata.get(key) == value for key, value in metadata_filter.items()
    )


class InMemoryBM25Retriever:
    """基于内存倒排索引的确定性 BM25 Retriever。

    参数：
    - `k1`：词频饱和参数，默认 1.5；
    - `b`：文档长度归一化强度，默认 0.75（0 表示完全不惩罚长度）。
    """

    def __init__(
        self,
        chunks: Sequence[Chunk],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._chunks = list(chunks)
        if any(not isinstance(chunk, Chunk) for chunk in self._chunks):
            raise TypeError("chunks must contain Chunk values")

        self._k1 = _validate_float_param(k1, "k1", minimum=0.0)
        self._b = _validate_float_param(b, "b", minimum=0.0)
        if self._b > 1.0:
            raise ValueError("b must be <= 1.0")

        self._term_frequencies: list[dict[str, int]] = []
        self._doc_lengths: list[int] = []
        self._postings: dict[str, list[int]] = {}

        for index, chunk in enumerate(self._chunks):
            terms = tokenize(chunk.content)
            frequencies: dict[str, int] = {}
            for term in terms:
                frequencies[term] = frequencies.get(term, 0) + 1
            self._term_frequencies.append(frequencies)
            self._doc_lengths.append(len(terms))

            for term in set(terms):
                self._postings.setdefault(term, []).append(index)

        self._avgdl = (
            sum(self._doc_lengths) / len(self._chunks) if self._chunks else 0.0
        )

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        """按 BM25 打分返回 top-k ChunkHit。"""
        validate_query(query)
        validate_top_k(top_k)
        if metadata_filter is not None and not isinstance(metadata_filter, Mapping):
            raise TypeError("metadata_filter must be a mapping")

        if not self._chunks:
            return []

        query_terms = set(tokenize(query))
        if not query_terms or self._avgdl <= 0:
            return []

        doc_count = len(self._chunks)
        scores: dict[int, float] = {}

        for term in query_terms:
            posting = self._postings.get(term)
            if posting is None:
                continue

            doc_frequency = len(posting)
            idf = math.log(
                1.0
                + (doc_count - doc_frequency + 0.5) / (doc_frequency + 0.5)
            )

            for index in posting:
                if not _matches(self._chunks[index], metadata_filter):
                    continue

                term_frequency = self._term_frequencies[index].get(term, 0)
                doc_length = self._doc_lengths[index]
                denominator = term_frequency + self._k1 * (
                    1.0
                    - self._b
                    + self._b * doc_length / self._avgdl
                )
                score = idf * (
                    term_frequency * (self._k1 + 1.0) / denominator
                )
                scores[index] = scores.get(index, 0.0) + score

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
