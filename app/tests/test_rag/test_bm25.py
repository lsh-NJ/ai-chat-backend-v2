import pytest

from app.rag.bm25 import InMemoryBM25Retriever, tokenize
from app.rag.chunking import Chunk
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


def test_tokenize_normalizes_ascii_and_keeps_cjk_terms() -> None:
    assert tokenize("RAG is great!") == ["rag", "is", "great"]
    assert tokenize("RAG检索增强") == ["rag", "检", "索", "增", "强"]
    assert tokenize("") == []


def test_tokenize_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="string"):
        tokenize(123)  # type: ignore[arg-type]


def test_bm25_ranks_higher_term_frequency_first() -> None:
    chunks = [
        make_chunk("apple banana", chunk_id="low"),
        make_chunk("apple banana banana banana", chunk_id="high"),
        make_chunk("orange only", chunk_id="none"),
    ]
    retriever = InMemoryBM25Retriever(chunks)

    hits = retriever.search("banana", top_k=3)

    assert [hit.chunk.id for hit in hits] == ["high", "low"]
    assert hits[0].score > hits[1].score
    assert [hit.rank for hit in hits] == [1, 2]
    assert hits[0].chunk.source == "docs/high.md"


def test_bm25_supports_chinese_query() -> None:
    chunks = [
        make_chunk(
            "用户可以在七天内申请退款",
            chunk_id="refund",
            metadata={"lang": "zh"},
        ),
        make_chunk(
            "退货需要提供订单号",
            chunk_id="return",
            metadata={"lang": "zh"},
        ),
    ]
    retriever = InMemoryBM25Retriever(chunks)

    hits = retriever.search("退款", top_k=2)

    assert hits[0].chunk.id == "refund"


def test_bm25_metadata_filter_filters_before_ranking() -> None:
    chunks = [
        make_chunk("python docs", chunk_id="public-en", metadata={"lang": "en", "tenant": "public"}),
        make_chunk("python 文档", chunk_id="internal-zh", metadata={"lang": "zh", "tenant": "internal"}),
        make_chunk("python private", chunk_id="internal-en", metadata={"lang": "en", "tenant": "internal"}),
    ]
    retriever = InMemoryBM25Retriever(chunks)

    zh = retriever.search("python", top_k=10, metadata_filter={"lang": "zh"})
    internal = retriever.search("python", top_k=10, metadata_filter={"tenant": "internal"})

    assert [hit.chunk.id for hit in zh] == ["internal-zh"]
    assert {hit.chunk.id for hit in internal} == {"internal-zh", "internal-en"}


def test_bm25_search_is_deterministic() -> None:
    chunks = [
        make_chunk("apple banana", chunk_id="b"),
        make_chunk("apple banana banana", chunk_id="a"),
        make_chunk("cherry", chunk_id="c"),
    ]
    retriever = InMemoryBM25Retriever(chunks)

    first = retriever.search("apple banana", top_k=2)
    second = retriever.search("apple banana", top_k=2)

    assert [(hit.chunk.id, hit.score, hit.rank) for hit in first] == [
        (hit.chunk.id, hit.score, hit.rank) for hit in second
    ]


def test_bm25_tie_break_is_deterministic_by_chunk_id() -> None:
    chunks = [
        make_chunk("same words", chunk_id="b"),
        make_chunk("same words", chunk_id="a"),
    ]
    retriever = InMemoryBM25Retriever(chunks)

    hits = retriever.search("same words", top_k=2)

    assert [hit.chunk.id for hit in hits] == ["a", "b"]


def test_bm25_no_match_returns_empty() -> None:
    retriever = InMemoryBM25Retriever(
        [make_chunk("apple banana", chunk_id="a")]
    )

    assert retriever.search("nonexistentterm", top_k=10) == []


def test_bm25_empty_corpus_returns_empty() -> None:
    retriever = InMemoryBM25Retriever([])

    assert retriever.search("anything", top_k=10) == []


def test_bm25_satisfies_retriever_protocol() -> None:
    retriever = InMemoryBM25Retriever(
        [make_chunk("apple", chunk_id="a")]
    )

    assert isinstance(retriever, Retriever)


def test_bm25_rejects_invalid_search_inputs() -> None:
    retriever = InMemoryBM25Retriever(
        [make_chunk("apple banana", chunk_id="a")]
    )

    with pytest.raises((TypeError, ValueError), match="query"):
        retriever.search("", top_k=10)
    with pytest.raises((TypeError, ValueError), match="top_k"):
        retriever.search("apple", top_k=0)
    with pytest.raises(TypeError, match="metadata_filter"):
        retriever.search("apple", top_k=10, metadata_filter="lang=zh")  # type: ignore[arg-type]


def test_bm25_rejects_invalid_constructor_arguments() -> None:
    chunk = make_chunk("apple", chunk_id="a")

    with pytest.raises(TypeError, match="Chunk"):
        InMemoryBM25Retriever(["not a chunk"])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="k1"):
        InMemoryBM25Retriever([chunk], k1=-1)
    with pytest.raises(ValueError, match="b"):
        InMemoryBM25Retriever([chunk], b=1.5)
