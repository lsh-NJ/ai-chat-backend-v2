"""Week 15 Day 4：Hybrid retriever 组合逻辑测试（不依赖数据库）。"""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.rag.hybrid import AsyncHybridRetriever
from app.rag.retrieval import AsyncRetriever, ChunkHit
from app.tests.test_rag.test_fusion import make_hit


class FakeRetriever:
    """记录调用参数并返回固定榜单的假异步检索器。"""

    def __init__(self, hits: Sequence[ChunkHit]) -> None:
        self._hits = tuple(hits)
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "metadata_filter": metadata_filter,
            }
        )
        return self._hits[:top_k]


async def test_hybrid_fuses_sparse_and_dense_results() -> None:
    sparse = FakeRetriever([make_hit("a"), make_hit("b")])
    dense = FakeRetriever([make_hit("b"), make_hit("c")])
    hybrid = AsyncHybridRetriever(sparse, dense, candidate_multiplier=4)

    hits = await hybrid.search("query", top_k=2)

    assert isinstance(hybrid, AsyncRetriever)
    assert [hit.chunk.id for hit in hits] == ["b", "a"]
    assert [hit.rank for hit in hits] == [1, 2]
    assert sparse.calls[0]["top_k"] == 8
    assert dense.calls[0]["top_k"] == 8
    assert sparse.calls[0]["query"] == "query"
    assert dense.calls[0]["query"] == "query"


async def test_hybrid_forwards_metadata_filter_to_both_branches() -> None:
    sparse = FakeRetriever([])
    dense = FakeRetriever([])
    hybrid = AsyncHybridRetriever(sparse, dense)

    await hybrid.search("query", metadata_filter={"lang": "en"})

    assert sparse.calls[0]["metadata_filter"] == {"lang": "en"}
    assert dense.calls[0]["metadata_filter"] == {"lang": "en"}


async def test_hybrid_returns_other_branch_when_one_branch_is_empty() -> None:
    sparse = FakeRetriever([])
    dense = FakeRetriever([make_hit("dense-only", rank=1)])
    hybrid = AsyncHybridRetriever(sparse, dense)

    hits = await hybrid.search("query", top_k=1)

    assert [hit.chunk.id for hit in hits] == ["dense-only"]


def test_hybrid_rejects_non_retriever_branches() -> None:
    with pytest.raises(TypeError, match="sparse_retriever"):
        AsyncHybridRetriever(object(), FakeRetriever([]))  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="dense_retriever"):
        AsyncHybridRetriever(FakeRetriever([]), object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "exception", "message"),
    [
        ({"rrf_k": 0}, ValueError, "rrf_k must be positive"),
        ({"candidate_multiplier": 0}, ValueError, "candidate_multiplier must be positive"),
        ({"rrf_k": True}, TypeError, "rrf_k must be an integer"),
    ],
)
def test_hybrid_validates_configuration(
    kwargs: dict[str, object],
    exception: type[Exception],
    message: str,
) -> None:
    with pytest.raises(exception, match=message):
        AsyncHybridRetriever(FakeRetriever([]), FakeRetriever([]), **kwargs)
