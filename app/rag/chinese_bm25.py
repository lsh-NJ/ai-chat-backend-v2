"""中文 BM25 稀疏检索器（Week 16 Day 6）。

实现边界：
- 使用 `jieba` 做中文分词；
- 使用 `rank_bm25.BM25Plus` 计算 BM25 分数。BM25Plus 是 BM25 的
  下界改进变体，在极小语料里也能避免 IDF 恰好为 0 导致全部分数为 0；
  `BM25Okapi` 在只有 2～4 条文档时很容易出现这种退化。
- 检索对象是内存中的 `Chunk` 列表，适合评测和中小规模语料；
- 生产级大规模语料可以把分词后的倒排索引下沉到 PostgreSQL / 搜索服务。

同步版实现 `Retriever`；`AsyncChineseBM25Retriever` 只是异步包装，
方便接入现有的 `AsyncHybridRetriever`。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import jieba
from rank_bm25 import BM25Plus

from app.rag.chunking import Chunk
from app.rag.retrieval import (
    ChunkHit,
    matches_metadata,
    validate_query,
    validate_top_k,
)

jieba.setLogLevel(logging.WARNING)


def tokenize_chinese(text: str) -> list[str]:
    """用 jieba 搜索模式分词，并过滤空白/纯标点 token。"""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return [
        token.strip().lower()
        for token in jieba.lcut_for_search(text)
        if token.strip()
        and any(
            char.isalnum() or "\u4e00" <= char <= "\u9fff"
            for char in token.strip()
        )
    ]


class ChineseBM25Retriever:
    """基于 jieba + rank_bm25 的同步中文 BM25 检索器。"""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if isinstance(chunks, (str, bytes)) or not isinstance(chunks, Sequence):
            raise TypeError("chunks must be a sequence of Chunk values")
        if any(not isinstance(chunk, Chunk) for chunk in chunks):
            raise TypeError("chunks must contain Chunk values")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b must be between 0 and 1")
        if k1 < 0:
            raise ValueError("k1 must be non-negative")

        self._chunks = tuple(chunks)
        self._tokenized_corpus = [
            tokenize_chinese(chunk.content) for chunk in self._chunks
        ]
        self._bm25 = (
            BM25Plus(self._tokenized_corpus, k1=k1, b=b)
            if self._tokenized_corpus
            else None
        )

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        """按 BM25 分数返回 top-k ChunkHit。"""
        validate_query(query)
        validate_top_k(top_k)
        if metadata_filter is not None and not isinstance(metadata_filter, Mapping):
            raise TypeError("metadata_filter must be a mapping")
        if self._bm25 is None:
            return ()

        query_tokens = tokenize_chinese(query)
        if not query_tokens:
            return ()

        scores = self._bm25.get_scores(query_tokens)
        candidates: list[tuple[float, Chunk]] = []
        for chunk, score in zip(self._chunks, scores, strict=True):
            if score <= 0:
                continue
            if not matches_metadata(chunk, metadata_filter):
                continue
            candidates.append((float(score), chunk))

        ranked = sorted(candidates, key=lambda item: (-item[0], item[1].id))
        return tuple(
            ChunkHit(chunk=chunk, score=score, rank=rank)
            for rank, (score, chunk) in enumerate(ranked[:top_k], start=1)
        )


class AsyncChineseBM25Retriever:
    """`ChineseBM25Retriever` 的异步适配器，供 hybrid 使用。"""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._retriever = ChineseBM25Retriever(
            chunks,
            k1=k1,
            b=b,
        )

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        return self._retriever.search(
            query,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )
