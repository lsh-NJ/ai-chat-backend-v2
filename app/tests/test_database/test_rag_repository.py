"""Week 15 Day 2：PostgreSQL 文档/chunk 仓库的真实数据库验收。"""

import pytest

from app.db.session import AsyncSessionFactory
from app.models.rag import RagDocument
from app.rag.chunking import Chunk
from app.rag.documents import Document
from app.rag.ingestion import (
    IngestionStatus,
    ingest_document_async,
    ingest_documents_async,
)
from app.rag.postgres_store import PostgresChunkStore, PostgresDocumentStore
from app.rag.store import (
    DocumentNotFoundError,
    DuplicateContentError,
    DuplicateIdError,
    VersionConflictError,
)


def make_document(
    source: str,
    content: str,
    *,
    document_id: str,
    version: int = 1,
) -> Document:
    return Document(
        id=document_id,
        source=source,
        content=content,
        metadata={"version": version},
    )


def make_chunk(
    *,
    chunk_id: str,
    document_id: str,
    content: str,
    start: int,
    end: int,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id=document_id,
        source="docs/a.md",
        content=content,
        metadata={"chunk_index": 0},
        start=start,
        end=end,
    )


async def test_document_repository_roundtrip_is_tenant_scoped(fresh_schema) -> None:
    document = make_document("docs/a.md", "租户 A 的内容", document_id="doc-1")

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        await store.save(document)
        await session.commit()

    async with AsyncSessionFactory() as session:
        tenant_a = PostgresDocumentStore(session, tenant_id="tenant-a")
        found = await tenant_a.get_by_content_hash(document.content_hash)

        assert found is not None
        assert found.id == "doc-1"
        assert found.metadata["version"] == 1
        assert await tenant_a.contains_content_hash(document.content_hash) is True
        assert [item.id for item in await tenant_a.list_documents()] == ["doc-1"]

        tenant_b = PostgresDocumentStore(session, tenant_id="tenant-b")
        assert await tenant_b.get_by_content_hash(document.content_hash) is None
        assert await tenant_b.list_documents() == ()


async def test_document_repository_rejects_duplicate_content_hash(
    fresh_schema,
) -> None:
    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        await store.save(
            make_document("docs/a.md", "相同正文", document_id="doc-1")
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        with pytest.raises(DuplicateContentError):
            await store.save(
                make_document("docs/b.md", "相同正文", document_id="doc-2")
            )


async def test_document_repository_rejects_duplicate_document_id(
    fresh_schema,
) -> None:
    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        await store.save(
            make_document("docs/a.md", "第一份", document_id="doc-1")
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        with pytest.raises(DuplicateIdError):
            await store.save(
                make_document("docs/a.md", "第二份", document_id="doc-1")
            )


async def test_document_savepoint_preserves_earlier_write_on_conflict(
    fresh_schema,
) -> None:
    first = make_document("docs/a.md", "保留我", document_id="doc-1")
    conflicting = make_document("docs/b.md", "保留我", document_id="doc-2")

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        await store.save(first)
        with pytest.raises(DuplicateContentError):
            await store.save(conflicting)
        await session.commit()

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        assert await store.contains_content_hash(first.content_hash) is True


async def test_document_repository_rejects_same_source_version(
    fresh_schema,
) -> None:
    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        await store.save(
            make_document(
                "docs/a.md",
                "第一版",
                document_id="doc-1::v1",
                version=1,
            )
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        with pytest.raises(VersionConflictError):
            await store.save(
                make_document(
                    "docs/a.md",
                    "另一个第一版",
                    document_id="doc-2::v1",
                    version=1,
                )
            )


async def test_async_ingestion_creates_skips_and_versions(fresh_schema) -> None:
    base = make_document("docs/a.md", "第一版内容", document_id="doc-1")

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        first = await ingest_document_async(store, base)
        await session.commit()

    assert first.status == IngestionStatus.CREATED
    assert first.version == 1
    assert first.document.id == "doc-1::v1"

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        duplicate = await ingest_document_async(store, base)
        await session.commit()

    assert duplicate.status == IngestionStatus.SKIPPED
    assert duplicate.version == 1
    assert duplicate.document.id == "doc-1::v1"

    changed = make_document("docs/a.md", "第二版内容", document_id="doc-1")
    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        second = await ingest_document_async(store, changed)
        await session.commit()

    assert second.status == IngestionStatus.UPDATED
    assert second.version == 2
    assert second.document.id == "doc-1::v2"


async def test_async_batch_ingestion_is_idempotent(fresh_schema) -> None:
    documents = [
        make_document("docs/a.md", "文档 A", document_id="doc-a"),
        make_document("docs/b.md", "文档 B", document_id="doc-b"),
    ]

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        first = await ingest_documents_async(store, documents)
        await session.commit()

    assert (first.created, first.skipped, first.updated) == (2, 0, 0)

    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        second = await ingest_documents_async(store, documents)
        await session.commit()

    assert (second.created, second.skipped, second.updated) == (0, 2, 0)


async def test_chunk_repository_saves_lists_and_skips_duplicates(
    fresh_schema,
) -> None:
    document = make_document("docs/a.md", "abcdef", document_id="doc-1::v1")
    chunks = [
        make_chunk(
            chunk_id="doc-1::chunk-0-3",
            document_id="doc-1::v1",
            content="abc",
            start=0,
            end=3,
        ),
        make_chunk(
            chunk_id="doc-1::chunk-3-6",
            document_id="doc-1::v1",
            content="def",
            start=3,
            end=6,
        ),
    ]

    async with AsyncSessionFactory() as session:
        await PostgresDocumentStore(session, tenant_id="tenant-a").save(document)
        chunk_store = PostgresChunkStore(session, tenant_id="tenant-a")
        await chunk_store.save_chunks("doc-1::v1", chunks)
        await session.commit()

    async with AsyncSessionFactory() as session:
        chunk_store = PostgresChunkStore(session, tenant_id="tenant-a")
        found = await chunk_store.list_chunks_by_document("doc-1::v1")
        assert [chunk.id for chunk in found] == [
            "doc-1::chunk-0-3",
            "doc-1::chunk-3-6",
        ]
        assert [chunk.content for chunk in found] == ["abc", "def"]

        # 重复保存同一批 chunk 是幂等的，不会产生重复行。
        await chunk_store.save_chunks("doc-1::v1", chunks)
        await session.commit()

    async with AsyncSessionFactory() as session:
        chunk_store = PostgresChunkStore(session, tenant_id="tenant-a")
        assert len(await chunk_store.list_chunks_by_document("doc-1::v1")) == 2


async def test_chunk_repository_is_tenant_scoped(fresh_schema) -> None:
    document = make_document("docs/a.md", "abcdef", document_id="doc-1::v1")
    chunk = make_chunk(
        chunk_id="doc-1::chunk-0-3",
        document_id="doc-1::v1",
        content="abc",
        start=0,
        end=3,
    )

    async with AsyncSessionFactory() as session:
        await PostgresDocumentStore(session, tenant_id="tenant-a").save(document)
        await PostgresChunkStore(session, tenant_id="tenant-a").save_chunks(
            "doc-1::v1",
            [chunk],
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        other_tenant = PostgresChunkStore(session, tenant_id="tenant-b")
        assert await other_tenant.list_chunks_by_document("doc-1::v1") == ()


async def test_chunk_repository_rejects_missing_document(fresh_schema) -> None:
    chunk = make_chunk(
        chunk_id="missing::chunk-0-3",
        document_id="missing::v1",
        content="abc",
        start=0,
        end=3,
    )

    async with AsyncSessionFactory() as session:
        store = PostgresChunkStore(session, tenant_id="tenant-a")
        with pytest.raises(DocumentNotFoundError):
            await store.save_chunks("missing::v1", [chunk])


async def test_chunk_repository_rejects_mismatched_document_id(
    fresh_schema,
) -> None:
    document = make_document("docs/a.md", "abcdef", document_id="doc-1::v1")
    chunk = make_chunk(
        chunk_id="doc-1::chunk-0-3",
        document_id="doc-1::v1",
        content="abc",
        start=0,
        end=3,
    )

    async with AsyncSessionFactory() as session:
        await PostgresDocumentStore(session, tenant_id="tenant-a").save(document)
        store = PostgresChunkStore(session, tenant_id="tenant-a")
        with pytest.raises(ValueError, match="document_id"):
            await store.save_chunks("another-doc::v1", [chunk])


async def test_document_repository_reads_back_version_column(fresh_schema) -> None:
    async with AsyncSessionFactory() as session:
        store = PostgresDocumentStore(session, tenant_id="tenant-a")
        await store.save(
            make_document(
                "docs/a.md",
                "版本测试",
                document_id="doc-1::v3",
                version=3,
            )
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        document = await session.get(
            RagDocument,
            {"tenant_id": "tenant-a", "document_id": "doc-1::v3"},
        )

    assert document is not None
    assert document.version == 3
