import pytest

from app.rag.chunking import Chunk, chunk_document
from app.rag.documents import Document


def make_document(content: str, metadata: dict | None = None) -> Document:
    return Document(
        id="doc-1",
        source="docs/test.md",
        content=content,
        metadata=metadata or {},
    )


def test_chunk_document_splits_without_overlap() -> None:
    document = make_document("0123456789")
    chunks = chunk_document(document, chunk_size=4, overlap=0)

    assert [c.content for c in chunks] == ["0123", "4567", "89"]
    assert [(c.start, c.end) for c in chunks] == [(0, 4), (4, 8), (8, 10)]


def test_chunk_document_supports_overlap() -> None:
    document = make_document("0123456789")
    chunks = chunk_document(document, chunk_size=5, overlap=2)

    assert [c.content for c in chunks] == ["01234", "34567", "6789"]
    assert [(c.start, c.end) for c in chunks] == [(0, 5), (3, 8), (6, 10)]
    # 第一个和第二个 chunk 应该有重叠部分
    assert chunks[0].content[-2:] == "34"
    assert chunks[1].content[:2] == "34"


def test_chunk_inherits_document_identity_and_metadata() -> None:
    document = make_document(
        "0123456789",
        metadata={"title": "测试文档", "lang": "zh"},
    )
    chunks = chunk_document(document, chunk_size=4, overlap=1)

    assert len(chunks) == 3
    for index, chunk in enumerate(chunks):
        assert isinstance(chunk, Chunk)
        assert chunk.document_id == "doc-1"
        assert chunk.source == "docs/test.md"
        assert chunk.metadata["title"] == "测试文档"
        assert chunk.metadata["lang"] == "zh"
        assert chunk.metadata["chunk_index"] == index
        assert chunk.metadata["chunk_count"] == len(chunks)


def test_chunk_metadata_is_read_only() -> None:
    document = make_document("0123456789")
    chunks = chunk_document(document, chunk_size=4)

    with pytest.raises(TypeError):
        chunks[0].metadata["title"] = "不可修改"  # type: ignore[index]


def test_chunk_content_matches_document_slice() -> None:
    content = "第一段。\n\n第二段。\n\n第三段。"
    document = make_document(content)
    chunks = chunk_document(document, chunk_size=5, overlap=1)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.content == content[chunk.start : chunk.end]


def test_short_document_returns_single_chunk() -> None:
    document = make_document("short")
    chunks = chunk_document(document, chunk_size=100, overlap=0)

    assert len(chunks) == 1
    assert chunks[0].content == "short"
    assert chunks[0].start == 0
    assert chunks[0].end == len("short")


def test_whitespace_only_windows_are_skipped() -> None:
    content = "hello" + " " * 10 + "world"
    document = make_document(content)
    chunks = chunk_document(document, chunk_size=5, overlap=0)

    assert len(chunks) == 2
    assert chunks[0].content == "hello"
    assert chunks[1].content == "world"
    assert chunks[0].end == 5
    assert chunks[1].start == 15


def test_chunk_ids_are_deterministic() -> None:
    document = make_document("0123456789")
    first = chunk_document(document, chunk_size=4)
    second = chunk_document(document, chunk_size=4)

    assert [c.id for c in first] == [c.id for c in second]


@pytest.mark.parametrize(
    ("chunk_size", "overlap", "match"),
    [
        (0, 0, "chunk_size"),
        (-1, 0, "chunk_size"),
        (4, -1, "overlap"),
        (4, 4, "overlap"),
        (4, 5, "overlap"),
    ],
)
def test_chunk_document_rejects_invalid_params(chunk_size, overlap, match) -> None:
    document = make_document("0123456789")

    with pytest.raises((TypeError, ValueError), match=match):
        chunk_document(document, chunk_size=chunk_size, overlap=overlap)


def test_chunk_document_rejects_non_document() -> None:
    with pytest.raises(TypeError, match="document"):
        chunk_document("not a document", chunk_size=10)  # type: ignore[arg-type]
