from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from app.rag.chunking import Chunk, chunk_document
from app.rag.documents import Document
from app.rag.retrieval import (
    ChunkHit,
    Retriever,
    validate_query,
    validate_top_k,
)


def make_chunk() -> Chunk:
    document = Document(
        id="doc-1",
        source="docs/guide.md",
        content="RAG 检索增强生成",
        metadata={"title": "使用指南", "lang": "zh"},
    )
    return chunk_document(document, chunk_size=10)[0]


def test_chunk_hit_preserves_traceable_source() -> None:
    chunk = make_chunk()
    hit = ChunkHit(chunk=chunk, score=1.0, rank=1)

    assert hit.chunk.document_id == "doc-1"
    assert hit.chunk.source == "docs/guide.md"
    assert hit.chunk.metadata["title"] == "使用指南"
    assert hit.chunk.metadata["lang"] == "zh"
    assert hit.chunk.content == "RAG 检索增强生成"[hit.chunk.start : hit.chunk.end]


def test_chunk_hit_is_immutable() -> None:
    hit = ChunkHit(chunk=make_chunk(), score=0.5, rank=2)

    with pytest.raises(FrozenInstanceError):
        hit.score = 9.0  # type: ignore[misc]


def test_chunk_hit_rejects_non_chunk() -> None:
    with pytest.raises(TypeError, match="Chunk"):
        ChunkHit(chunk="not-a-chunk", score=1.0, rank=1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("score", "error_type", "match"),
    [
        (float("nan"), ValueError, "finite"),
        (float("inf"), ValueError, "finite"),
        ("high", TypeError, "number"),
        (True, TypeError, "number"),
    ],
)
def test_chunk_hit_rejects_invalid_score(score, error_type, match) -> None:
    with pytest.raises(error_type, match=match):
        ChunkHit(chunk=make_chunk(), score=score, rank=1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rank", "error_type", "match"),
    [
        (0, ValueError, "positive"),
        (-1, ValueError, "positive"),
        (True, TypeError, "integer"),
        ("1", TypeError, "integer"),
    ],
)
def test_chunk_hit_rejects_invalid_rank(rank, error_type, match) -> None:
    with pytest.raises(error_type, match=match):
        ChunkHit(chunk=make_chunk(), score=1.0, rank=rank)  # type: ignore[arg-type]


class FakeRetriever:
    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        return []


class NotARetriever:
    def unrelated(self) -> None:
        pass


def test_retriever_is_structural_protocol() -> None:
    assert isinstance(FakeRetriever(), Retriever)
    assert not isinstance(NotARetriever(), Retriever)
    assert not isinstance(object(), Retriever)


@pytest.mark.parametrize(
    "query",
    ["", "   ", 123, None],
)
def test_validate_query_fails_closed(query) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_query(query)  # type: ignore[arg-type]


def test_validate_query_accepts_non_empty_string() -> None:
    validate_query("  RAG 检索  ")


@pytest.mark.parametrize(
    "top_k",
    [0, -1, True, 1.5, "10"],
)
def test_validate_top_k_fails_closed(top_k) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_top_k(top_k)  # type: ignore[arg-type]


def test_validate_top_k_accepts_positive_integer() -> None:
    validate_top_k(10)
