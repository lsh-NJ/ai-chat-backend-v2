import math

import pytest

from app.rag.bm25 import tokenize
from app.rag.chunking import Chunk
from app.rag.dense import (
    InMemoryDenseRetriever,
    cosine_similarity,
    hash_embed,
)
from app.rag.retrieval import Retriever


def make_chunk(
    content: str,
    *,
    chunk_id: str,
    metadata: dict | None = None,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id=f"doc-{chunk_id}",
        source=f"docs/{chunk_id}.md",
        content=content,
        metadata=metadata or {},
        start=0,
        end=len(content),
    )


def make_toy_embedder():
    def embed(text: str) -> list[float]:
        terms = set(tokenize(text))
        return [
            1.0 if "fruit" in terms else 0.0,
            1.0 if "apple" in terms else 0.0,
            1.0 if "car" in terms else 0.0,
        ]

    return embed


def test_cosine_similarity_known_values() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0, 1.0], [1.0, 0.0]) == pytest.approx(
        1.0 / math.sqrt(2)
    )


@pytest.mark.parametrize(
    ("a", "b", "error_type", "match"),
    [
        ([], [1.0], ValueError, "empty"),
        ([1.0, 2.0], [1.0], ValueError, "same dimension"),
        ([0.0, 0.0], [1.0, 0.0], ValueError, "zero vector"),
        ("ab", [1.0, 0.0], TypeError, "sequence"),
        ([float("nan")], [1.0], ValueError, "finite"),
        ([float("inf")], [1.0], ValueError, "finite"),
    ],
)
def test_cosine_similarity_fails_closed(a, b, error_type, match) -> None:
    with pytest.raises(error_type, match=match):
        cosine_similarity(a, b)  # type: ignore[arg-type]


def test_hash_embed_is_deterministic_and_fixed_dimension() -> None:
    first = hash_embed("RAG 检索")
    second = hash_embed("RAG 检索")

    assert first == second
    assert len(first) == 128
    assert all(isinstance(value, float) for value in first)


def test_hash_embed_rejects_invalid_dimensions() -> None:
    with pytest.raises((TypeError, ValueError), match="dimensions"):
        hash_embed("text", dimensions=0)  # type: ignore[arg-type]


def test_dense_retriever_ranks_semantic_vector_first() -> None:
    chunks = [
        make_chunk("fruit apple salad", chunk_id="fruit"),
        make_chunk("car engine", chunk_id="car"),
        make_chunk("apple pie", chunk_id="apple"),
    ]
    retriever = InMemoryDenseRetriever(chunks, embedder=make_toy_embedder())

    hits = retriever.search("apple fruit", top_k=3)

    assert [hit.chunk.id for hit in hits] == ["fruit", "apple", "car"]
    assert hits[0].score > hits[1].score
    assert hits[0].score > hits[2].score
    assert [hit.rank for hit in hits] == [1, 2, 3]


def test_dense_retriever_metadata_filter() -> None:
    chunks = [
        make_chunk(
            "python docs",
            chunk_id="en",
            metadata={"lang": "en", "tenant": "public"},
        ),
        make_chunk(
            "python 文档",
            chunk_id="zh",
            metadata={"lang": "zh", "tenant": "internal"},
        ),
    ]
    retriever = InMemoryDenseRetriever(chunks, embedder=hash_embed)

    hits = retriever.search("python", top_k=10, metadata_filter={"lang": "zh"})

    assert [hit.chunk.id for hit in hits] == ["zh"]


def test_dense_retriever_search_is_deterministic() -> None:
    chunks = [
        make_chunk("apple banana", chunk_id="b"),
        make_chunk("apple banana banana", chunk_id="a"),
        make_chunk("cherry", chunk_id="c"),
    ]
    retriever = InMemoryDenseRetriever(chunks, embedder=hash_embed)

    first = retriever.search("apple banana", top_k=2)
    second = retriever.search("apple banana", top_k=2)

    assert [(hit.chunk.id, hit.score, hit.rank) for hit in first] == [
        (hit.chunk.id, hit.score, hit.rank) for hit in second
    ]


def test_dense_retriever_tie_break_is_deterministic_by_chunk_id() -> None:
    chunks = [
        make_chunk("same words", chunk_id="b"),
        make_chunk("same words", chunk_id="a"),
    ]
    retriever = InMemoryDenseRetriever(
        chunks,
        embedder=lambda text: [1.0],
    )

    hits = retriever.search("same words", top_k=2)

    assert [hit.chunk.id for hit in hits] == ["a", "b"]


def test_dense_retriever_satisfies_retriever_protocol() -> None:
    retriever = InMemoryDenseRetriever(
        [make_chunk("apple", chunk_id="a")],
        embedder=hash_embed,
    )

    assert isinstance(retriever, Retriever)


def test_dense_retriever_empty_corpus_returns_empty() -> None:
    retriever = InMemoryDenseRetriever([], embedder=hash_embed)

    assert retriever.search("anything", top_k=10) == []


def test_dense_retriever_rejects_invalid_embedder_outputs() -> None:
    with pytest.raises(ValueError, match="zero vector"):
        InMemoryDenseRetriever(
            [make_chunk("apple", chunk_id="a")],
            embedder=lambda text: [0.0],
        )
    with pytest.raises(ValueError, match="empty"):
        InMemoryDenseRetriever(
            [make_chunk("apple", chunk_id="a")],
            embedder=lambda text: [],
        )
    with pytest.raises(TypeError, match="callable"):
        InMemoryDenseRetriever(
            [make_chunk("apple", chunk_id="a")],
            embedder="not callable",  # type: ignore[arg-type]
        )


def test_dense_retriever_rejects_query_dimension_mismatch() -> None:
    def embedder(text: str) -> list[float]:
        if text == "query":
            return [1.0, 0.0]
        return [1.0]

    retriever = InMemoryDenseRetriever(
        [make_chunk("apple", chunk_id="a")],
        embedder=embedder,
    )

    with pytest.raises(ValueError, match="dimension"):
        retriever.search("query", top_k=10)


def test_dense_retriever_rejects_invalid_search_inputs() -> None:
    retriever = InMemoryDenseRetriever(
        [make_chunk("apple", chunk_id="a")],
        embedder=hash_embed,
    )

    with pytest.raises((TypeError, ValueError), match="query"):
        retriever.search("", top_k=10)
    with pytest.raises((TypeError, ValueError), match="top_k"):
        retriever.search("apple", top_k=0)
    with pytest.raises(TypeError, match="metadata_filter"):
        retriever.search("apple", top_k=10, metadata_filter="lang=zh")  # type: ignore[arg-type]
