"""Week 15 Day 1：pgvector 扩展与 RAG 表结构的真实数据库验收。

这些测试需要 PostgreSQL（CI 使用 pgvector 镜像），不能脱离数据库运行。
"""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.session import AsyncSessionFactory, engine
from app.models.rag import RAG_EMBEDDING_DIMENSION, RagChunk, RagDocument


async def test_pgvector_extension_is_enabled(fresh_schema) -> None:
    async with engine.connect() as conn:
        extension = (
            await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar_one()

    assert extension == "vector"


async def test_rag_chunks_full_text_gin_index_exists(fresh_schema) -> None:
    async with engine.connect() as conn:
        indexdef = (
            await conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND indexname = 'ix_rag_chunks_content_fts'"
                )
            )
        ).scalar_one()

    normalized = indexdef.lower()
    assert "using gin" in normalized
    assert "to_tsvector" in normalized
    assert "content" in normalized


async def test_rag_tables_exist_with_expected_columns(fresh_schema) -> None:
    async with engine.connect() as conn:
        tables = {
            row[0]
            for row in await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ("
                    "'rag_documents', 'rag_chunks', 'rag_ingestion_jobs'"
                    ")"
                )
            )
        }
        columns_by_table: dict[str, set[str]] = {}
        for table in tables:
            rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table"
                ),
                {"table": table},
            )
            columns_by_table[table] = {row[0] for row in rows}

    assert tables == {"rag_documents", "rag_chunks", "rag_ingestion_jobs"}
    assert {
        "tenant_id",
        "document_id",
        "source",
        "version",
        "content",
        "content_hash",
        "metadata",
        "created_at",
    } <= columns_by_table["rag_documents"]
    assert {
        "tenant_id",
        "chunk_id",
        "document_id",
        "source",
        "content",
        "metadata",
        "start",
        "end",
        "embedding",
        "created_at",
    } <= columns_by_table["rag_chunks"]
    assert {
        "tenant_id",
        "job_id",
        "filename",
        "content_type",
        "content_sha256",
        "storage_key",
        "size_bytes",
        "status",
        "attempts",
        "max_attempts",
        "error_message",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    } <= columns_by_table["rag_ingestion_jobs"]


async def test_rag_document_and_chunk_orm_roundtrip(fresh_schema) -> None:
    tenant_id = "tenant-a"
    document_id = "doc-1::v1"
    chunk_id = "doc-1::chunk-0-9"

    async with AsyncSessionFactory() as session:
        document = RagDocument(
            tenant_id=tenant_id,
            document_id=document_id,
            source="docs/rag.md",
            content="RAG 测试内容",
            content_hash="hash-a",
            doc_metadata={"title": "RAG 入门"},
        )
        chunk = RagChunk(
            tenant_id=tenant_id,
            chunk_id=chunk_id,
            document_id=document_id,
            source="docs/rag.md",
            content="RAG 测试",
            chunk_metadata={"chunk_index": 0},
            start=0,
            end=6,
            embedding=[0.1] * RAG_EMBEDDING_DIMENSION,
        )
        session.add_all([document, chunk])
        await session.commit()

    async with AsyncSessionFactory() as session:
        document = await session.get(
            RagDocument,
            {"tenant_id": tenant_id, "document_id": document_id},
        )
        chunk = await session.get(
            RagChunk,
            {"tenant_id": tenant_id, "chunk_id": chunk_id},
        )

    assert document is not None
    assert document.doc_metadata["title"] == "RAG 入门"
    assert chunk is not None
    assert chunk.document_id == document_id
    assert chunk.start == 0
    assert chunk.end == 6
    assert chunk.embedding is not None
    assert len(chunk.embedding) == RAG_EMBEDDING_DIMENSION
    assert chunk.embedding[0] == pytest.approx(0.1)


async def test_same_document_id_and_content_hash_are_tenant_scoped(
    fresh_schema,
) -> None:
    async with AsyncSessionFactory() as session:
        session.add_all(
            [
                RagDocument(
                    tenant_id="tenant-a",
                    document_id="doc-1::v1",
                    source="docs/a.md",
                    content="相同正文",
                    content_hash="same-hash",
                ),
                RagDocument(
                    tenant_id="tenant-b",
                    document_id="doc-1::v1",
                    source="docs/a.md",
                    content="相同正文",
                    content_hash="same-hash",
                ),
            ]
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        count = (
            await session.execute(select(func.count()).select_from(RagDocument))
        ).scalar_one()

    assert count == 2


async def test_same_tenant_duplicate_document_id_is_rejected(fresh_schema) -> None:
    async with AsyncSessionFactory() as session:
        session.add(
            RagDocument(
                tenant_id="tenant-a",
                document_id="doc-1::v1",
                source="docs/a.md",
                content="第一份",
                content_hash="hash-1",
            )
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        session.add(
            RagDocument(
                tenant_id="tenant-a",
                document_id="doc-1::v1",
                source="docs/a.md",
                content="第二份",
                content_hash="hash-2",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_same_tenant_duplicate_content_hash_is_rejected(fresh_schema) -> None:
    async with AsyncSessionFactory() as session:
        session.add(
            RagDocument(
                tenant_id="tenant-a",
                document_id="doc-1::v1",
                source="docs/a.md",
                content="相同正文",
                content_hash="same-hash",
            )
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        session.add(
            RagDocument(
                tenant_id="tenant-a",
                document_id="doc-2::v1",
                source="docs/b.md",
                content="相同正文",
                content_hash="same-hash",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_deleting_document_cascades_to_chunks(fresh_schema) -> None:
    tenant_id = "tenant-a"
    document_id = "doc-1::v1"

    async with AsyncSessionFactory() as session:
        document = RagDocument(
            tenant_id=tenant_id,
            document_id=document_id,
            source="docs/a.md",
            content="级联删除测试",
            content_hash="hash-cascade",
        )
        session.add(document)
        await session.flush()
        session.add(
            RagChunk(
                tenant_id=tenant_id,
                chunk_id="doc-1::chunk-0-4",
                document_id=document_id,
                source="docs/a.md",
                content="级联删除测试",
                start=0,
                end=6,
            )
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        document = await session.get(
            RagDocument,
            {"tenant_id": tenant_id, "document_id": document_id},
        )
        assert document is not None
        await session.delete(document)
        await session.commit()

    async with AsyncSessionFactory() as session:
        chunk_count = (
            await session.execute(
                select(func.count())
                .select_from(RagChunk)
                .where(RagChunk.tenant_id == tenant_id)
            )
        ).scalar_one()

    assert chunk_count == 0
