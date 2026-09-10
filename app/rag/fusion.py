"""RRF（Reciprocal Rank Fusion）融合算法（Week 15 Day 4）。

设计原则：
- 只依赖 `ChunkHit`，不依赖任何数据库或检索算法；
- 输入是多个“已按相关性降序排好的榜单”，输出融合后的新榜单；
- 不同检索器的原始分数不可比，所以只使用 rank，不使用 score；
- 相同分数按 chunk id 排序，保证结果确定。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.rag.chunking import Chunk
from app.rag.retrieval import ChunkHit


def _validate_positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def rrf_fuse(
    result_lists: Iterable[Sequence[ChunkHit]],
    *,
    k: int = 60,
    top_k: int = 10,
) -> tuple[ChunkHit, ...]:
    """用 RRF 融合多个检索榜单。

    - `result_lists`：每个元素是一个检索器返回的、已按相关性降序排列的榜单；
    - `k`：RRF 平滑常数，默认 60；值越大，越弱化“排名第一”的绝对优势；
    - `top_k`：融合后返回的候选数。

    不变量：
    - 同一个榜单里不能重复出现同一个 chunk.id；
    - 不同榜单里出现同一个 chunk 是正常的，分数会累加；
    - 如果同一个 chunk 在不同榜单里内容不一致，说明上游数据/查询边界有问题，直接报错。
    """
    validated_k = _validate_positive_int(k, "k")
    validated_top_k = _validate_positive_int(top_k, "top_k")

    scores: dict[str, float] = {}
    chunks: dict[str, Chunk] = {}

    for result_list in result_lists:
        if not isinstance(result_list, Sequence):
            raise TypeError("each result list must be a sequence")
        seen_in_list: set[str] = set()
        for rank, hit in enumerate(result_list, start=1):
            if not isinstance(hit, ChunkHit):
                raise TypeError("result lists must contain ChunkHit values")
            chunk_id = hit.chunk.id
            if chunk_id in seen_in_list:
                raise ValueError(
                    f"chunk id appears twice in one result list: {chunk_id}"
                )
            seen_in_list.add(chunk_id)

            existing = chunks.get(chunk_id)
            if existing is not None and existing != hit.chunk:
                raise ValueError(
                    f"chunk payload differs across result lists: {chunk_id}"
                )
            chunks.setdefault(chunk_id, hit.chunk)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (
                validated_k + rank
            )

    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], item[0]),
    )

    return tuple(
        ChunkHit(
            chunk=chunks[chunk_id],
            score=score,
            rank=rank,
        )
        for rank, (chunk_id, score) in enumerate(
            ranked[:validated_top_k],
            start=1,
        )
    )
