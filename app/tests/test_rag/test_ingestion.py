import pytest

from app.rag.documents import Document
from app.rag.ingestion import (
    IngestionStatus,
    ingest_document,
    ingest_documents,
)
from app.rag.store import (
    DuplicateContentError,
    InMemoryDocumentStore,
)


def make_document(
    source: str,
    content: str,
    document_id: str | None = None,
    metadata: dict | None = None,
) -> Document:
    return Document(
        id=document_id or f"{source}-{len(content)}",
        source=source,
        content=content,
        metadata=metadata or {},
    )


def test_ingest_new_document_creates_version_1() -> None:
    store = InMemoryDocumentStore()
    document = make_document("docs/a.md", "第一次内容")

    result = ingest_document(store, document)

    assert result.status == IngestionStatus.CREATED
    assert result.version == 1
    assert result.document.id.endswith("::v1")
    assert result.document.metadata["version"] == 1
    assert store.contains_content_hash(document.content_hash)


def test_ingest_same_content_is_skipped() -> None:
    store = InMemoryDocumentStore()
    first = make_document("docs/a.md", "相同内容", document_id="a")
    ingest_document(store, first)

    second = make_document("docs/a.md", "相同内容", document_id="a-copy")
    result = ingest_document(store, second)

    assert result.status == IngestionStatus.SKIPPED
    assert result.version == 1
    assert len(store.list_documents()) == 1
    assert len(store.get_versions_by_source("docs/a.md")) == 1


def test_ingest_same_content_from_different_source_is_skipped() -> None:
    store = InMemoryDocumentStore()
    ingest_document(store, make_document("docs/a.md", "转载正文", document_id="a"))
    result = ingest_document(
        store,
        make_document("docs/b.md", "转载正文", document_id="b"),
    )

    assert result.status == IngestionStatus.SKIPPED
    assert len(store.list_documents()) == 1


def test_ingest_changed_content_creates_new_version() -> None:
    store = InMemoryDocumentStore()
    v1 = make_document("docs/a.md", "第一版内容", document_id="a-v1")
    first = ingest_document(store, v1)

    v2 = make_document("docs/a.md", "第二版内容", document_id="a-v2")
    second = ingest_document(store, v2)

    assert first.status == IngestionStatus.CREATED
    assert first.version == 1
    assert second.status == IngestionStatus.UPDATED
    assert second.version == 2
    assert second.document.id.endswith("::v2")
    assert len(store.list_documents()) == 2
    assert len(store.get_versions_by_source("docs/a.md")) == 2


def test_ingest_documents_batch_is_idempotent() -> None:
    store = InMemoryDocumentStore()
    documents = [
        make_document("docs/a.md", "文档 A", document_id="a"),
        make_document("docs/b.md", "文档 B", document_id="b"),
    ]

    first = ingest_documents(store, documents)
    second = ingest_documents(store, documents)

    assert (first.created, first.skipped, first.updated) == (2, 0, 0)
    assert (second.created, second.skipped, second.updated) == (0, 2, 0)
    assert len(store.list_documents()) == 2


def test_ingest_batch_reports_mixed_statuses() -> None:
    store = InMemoryDocumentStore()
    documents = [
        make_document("docs/a.md", "新文档", document_id="a"),
        make_document("docs/b.md", "旧文档", document_id="b-old"),
        make_document("docs/b.md", "旧文档", document_id="b-copy"),
        make_document("docs/a.md", "新版本", document_id="a-new"),
    ]

    report = ingest_documents(store, documents)

    assert report.created == 2
    assert report.skipped == 1
    assert report.updated == 1
    assert len(report.results) == 4


def test_store_rejects_duplicate_content_directly() -> None:
    store = InMemoryDocumentStore()
    document = make_document("docs/a.md", "不能重复", document_id="a")
    store.save(document)

    with pytest.raises(DuplicateContentError):
        store.save(make_document("docs/b.md", "不能重复", document_id="b"))


def test_ingest_rejects_non_document() -> None:
    store = InMemoryDocumentStore()

    with pytest.raises(TypeError, match="document"):
        ingest_document(store, "not a document")  # type: ignore[arg-type]
