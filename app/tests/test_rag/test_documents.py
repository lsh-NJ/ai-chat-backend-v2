from dataclasses import FrozenInstanceError

import pytest

from app.rag.documents import Document, compute_content_hash


def test_document_accepts_valid_input_and_copies_metadata() -> None:
    source_metadata = {"title": "RAG 入门", "lang": "zh"}
    document = Document(
        id="doc-1",
        source="docs/rag-intro.md",
        content="# RAG 入门\n\nRAG 是检索增强生成。",
        metadata=source_metadata,
    )

    source_metadata["title"] = "已修改"

    assert document.id == "doc-1"
    assert document.source == "docs/rag-intro.md"
    assert document.metadata["title"] == "RAG 入门"
    assert document.content_hash == compute_content_hash(document.content)
    with pytest.raises(TypeError):
        document.metadata["title"] = "不可修改"  # type: ignore[index]


def test_content_hash_is_stable_and_content_sensitive() -> None:
    content = "hello"
    assert compute_content_hash(content) == compute_content_hash(content)
    assert compute_content_hash("hello") != compute_content_hash("hello ")


def test_document_computes_hash_automatically() -> None:
    document = Document(
        id="doc-1",
        source="s",
        content="abc",
    )

    assert document.content_hash == compute_content_hash("abc")
    with pytest.raises(FrozenInstanceError):
        document.content_hash = "fake"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("factory", "error_type", "message"),
    [
        (
            lambda: Document(id="", source="s", content="c"),
            ValueError,
            "id",
        ),
        (
            lambda: Document(id="d", source="", content="c"),
            ValueError,
            "source",
        ),
        (
            lambda: Document(id="d", source="s", content=""),
            ValueError,
            "content",
        ),
        (
            lambda: Document(id="d", source="s", content=123),  # type: ignore[arg-type]
            TypeError,
            "content",
        ),
    ],
)
def test_document_rejects_invalid_states(factory, error_type, message) -> None:
    with pytest.raises(error_type, match=message):
        factory()
