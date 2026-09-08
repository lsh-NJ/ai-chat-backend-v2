"""Week 14 Day 4：检索召回评测。

提供固定 golden set 上的三个基础指标：
- Recall@k：前 k 个结果召回了多少相关 chunk；
- Reciprocal Rank：第一个相关 chunk 排名的倒数；
- nDCG@k：同时考虑是否相关与相关结果的排序质量。

评测对象是 `Retriever` 协议，因此 BM25 和 dense retriever 可以在同一组
golden queries 上比较。
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from app.rag.retrieval import Retriever, validate_query, validate_top_k


@dataclass(frozen=True, slots=True)
class EvalQuery:
    """一条评测问题：query + 人工确认的相关 chunk ids。"""

    query: str
    relevant_chunk_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_query(self.query)
        if not isinstance(self.relevant_chunk_ids, tuple):
            raise TypeError("relevant_chunk_ids must be a tuple")
        if not self.relevant_chunk_ids:
            raise ValueError("relevant_chunk_ids must not be empty")
        if any(
            not isinstance(chunk_id, str) or not chunk_id.strip()
            for chunk_id in self.relevant_chunk_ids
        ):
            raise ValueError("relevant chunk ids must be non-empty strings")


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    """一组 golden query 上的平均检索指标。"""

    k: int
    query_count: int
    mean_recall_at_k: float
    mean_reciprocal_rank: float
    mean_ndcg_at_k: float


def _validate_retrieved_ids(retrieved_ids: object) -> Sequence[str]:
    if isinstance(retrieved_ids, (str, bytes)) or not isinstance(
        retrieved_ids, Sequence
    ):
        raise TypeError("retrieved_ids must be a sequence of chunk ids")
    return retrieved_ids


def _validate_relevant_ids(relevant_ids: object, k: int) -> set[str]:
    validate_top_k(k)
    if isinstance(relevant_ids, (str, bytes)) or not isinstance(
        relevant_ids, Collection
    ):
        raise TypeError("relevant_ids must be a collection of chunk ids")
    if not relevant_ids:
        raise ValueError("relevant_ids must not be empty")
    if any(
        not isinstance(chunk_id, str) or not chunk_id.strip()
        for chunk_id in relevant_ids
    ):
        raise ValueError("relevant chunk ids must be non-empty strings")
    return set(relevant_ids)


def recall_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Collection[str],
    *,
    k: int,
) -> float:
    """前 k 个结果中命中的相关 chunk 数 / 总相关 chunk 数。"""
    _validate_retrieved_ids(retrieved_ids)
    relevant = _validate_relevant_ids(relevant_ids, k)

    top = retrieved_ids[:k]
    hit_count = len({chunk_id for chunk_id in top if chunk_id in relevant})
    return hit_count / len(relevant)


def reciprocal_rank_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Collection[str],
    *,
    k: int,
) -> float:
    """第一个相关 chunk 排名的倒数；前 k 个没有相关 chunk 则为 0。"""
    _validate_retrieved_ids(retrieved_ids)
    relevant = _validate_relevant_ids(relevant_ids, k)

    for rank, chunk_id in enumerate(retrieved_ids[:k], start=1):
        if chunk_id in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Collection[str],
    *,
    k: int,
) -> float:
    """二进制相关性的 nDCG@k：越靠前的相关结果得分越高。"""
    _validate_retrieved_ids(retrieved_ids)
    relevant = _validate_relevant_ids(relevant_ids, k)

    dcg = 0.0
    for rank, chunk_id in enumerate(retrieved_ids[:k], start=1):
        if chunk_id in relevant:
            dcg += 1.0 / math.log2(rank + 1)

    ideal_count = min(k, len(relevant))
    idcg = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, ideal_count + 1)
    )
    return dcg / idcg


def evaluate_retriever(
    retriever: Retriever,
    queries: Sequence[EvalQuery],
    *,
    k: int,
) -> RetrievalReport:
    """在 golden queries 上运行 retriever，返回平均 Recall@k / MRR / nDCG@k。"""
    validate_top_k(k)
    if isinstance(queries, (str, bytes)) or not isinstance(queries, Sequence):
        raise TypeError("queries must be a sequence of EvalQuery values")
    if not queries:
        raise ValueError("queries must not be empty")

    total_recall = 0.0
    total_reciprocal_rank = 0.0
    total_ndcg = 0.0

    for eval_query in queries:
        if not isinstance(eval_query, EvalQuery):
            raise TypeError("queries must contain EvalQuery values")

        hits = retriever.search(eval_query.query, top_k=k)
        retrieved_ids = [hit.chunk.id for hit in hits]

        total_recall += recall_at_k(
            retrieved_ids,
            eval_query.relevant_chunk_ids,
            k=k,
        )
        total_reciprocal_rank += reciprocal_rank_at_k(
            retrieved_ids,
            eval_query.relevant_chunk_ids,
            k=k,
        )
        total_ndcg += ndcg_at_k(
            retrieved_ids,
            eval_query.relevant_chunk_ids,
            k=k,
        )

    query_count = len(queries)
    return RetrievalReport(
        k=k,
        query_count=query_count,
        mean_recall_at_k=total_recall / query_count,
        mean_reciprocal_rank=total_reciprocal_rank / query_count,
        mean_ndcg_at_k=total_ndcg / query_count,
    )
