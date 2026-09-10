"""Week 15 Day 4：RRF 纯函数测试（不依赖数据库）。"""

import pytest

from app.rag.chunking import Chunk
from app.rag.fusion import rrf_fuse
from app.rag.retrieval import ChunkHit


def make_chunk(chunk_id: str, content: str | None = None) -> Chunk:
    text = content or f"content-{chunk_id}"
    return Chunk(
        id=chunk_id,
        document_id="doc-1",
        source="docs/a.md",
        content=text,
        metadata={},
        start=0,
        end=len(text),
    )


def make_hit(chunk_id: str, *, rank: int = 1, score: float = 1.0) -> ChunkHit:
    return ChunkHit(chunk=make_chunk(chunk_id), score=score, rank=rank)


def test_rrf_fuse_sums_scores_for_chunk_in_multiple_lists() -> None:
    first = [make_hit("a"), make_hit("b")]
    second = [make_hit("c"), make_hit("a")]

    fused = rrf_fuse([first, second], k=60, top_k=3)

    assert [hit.chunk.id for hit in fused] == ["a", "c", "b"]
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert fused[1].score == pytest.approx(1 / 61)
    assert fused[2].score == pytest.approx(1 / 62)


def test_rrf_fuse_applies_top_k_and_renumbers_rank() -> None:
    first = [make_hit("a"), make_hit("b")]
    second = [make_hit("c"), make_hit("a")]

    fused = rrf_fuse([first, second], k=60, top_k=2)

    assert [hit.chunk.id for hit in fused] == ["a", "c"]
    assert [hit.rank for hit in fused] == [1, 2]


def test_rrf_fuse_breaks_score_ties_by_chunk_id() -> None:
    first = [make_hit("b")]
    second = [make_hit("a")]

    fused = rrf_fuse([first, second], k=60, top_k=2)

    assert [hit.chunk.id for hit in fused] == ["a", "b"]
    assert fused[0].score == pytest.approx(fused[1].score)


def test_rrf_fuse_does_not_depend_on_result_list_order() -> None:
    sparse = [make_hit("a"), make_hit("b")]
    dense = [make_hit("b"), make_hit("c")]

    sparse_first = rrf_fuse([sparse, dense], k=60, top_k=3)
    dense_first = rrf_fuse([dense, sparse], k=60, top_k=3)

    assert sparse_first == dense_first


def test_rrf_fuse_returns_empty_for_no_result_lists() -> None:
    assert rrf_fuse([], top_k=10) == ()


def test_rrf_fuse_rejects_duplicate_chunk_in_one_list() -> None:
    duplicate = [make_hit("a"), make_hit("a")]

    with pytest.raises(ValueError, match="appears twice"):
        rrf_fuse([duplicate])


def test_rrf_fuse_rejects_conflicting_chunk_payload() -> None:
    first = [ChunkHit(chunk=make_chunk("a", "first"), score=1.0, rank=1)]
    second = [ChunkHit(chunk=make_chunk("a", "second"), score=1.0, rank=1)]

    with pytest.raises(ValueError, match="payload differs"):
        rrf_fuse([first, second])


@pytest.mark.parametrize(
    ("kwargs", "exception", "message"),
    [
        ({"k": 0}, ValueError, "k must be positive"),
        ({"top_k": 0}, ValueError, "top_k must be positive"),
        ({"k": True}, TypeError, "k must be an integer"),
    ],
)
def test_rrf_fuse_validates_params(
    kwargs: dict[str, object],
    exception: type[Exception],
    message: str,
) -> None:
    with pytest.raises(exception, match=message):
        rrf_fuse([make_hit("a")], **kwargs)
