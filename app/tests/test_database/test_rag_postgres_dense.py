"""Week 15 Day 3：pgvector 稠密检索器的真实数据库验收。"""

from collections.abc import Sequence

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionFactory
from app.models.rag import RAG_EMBEDDING_DIMENSION, RagChunk
from app.rag.chunking import Chunk
from app.rag.documents import Document
from app.rag.postgres_dense import PostgresDenseRetriever
from app.rag.postgres_store import PostgresChunkStore, PostgresDocumentStore
from app.rag.retrieval import AsyncRetriever


def keyword_embedder(text: str) -> list[float]:
    """把关键词计数当成可预测的 128 维玩具 embedding。"""
    vector = [0.0] * RAG_EMBEDDING_DIMENSION
    lowered = text.lower()
    for index, term in enumerate(("apple", "banana", "car", "phone")):
        vector[index] = float(lowered.count(term))
    return vector


def make_chunk(
    *,
    chunk_id: str,
    document_id: str,
    content: str,
    metadata: dict | None = None,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id=document_id,
        source="docs/a.md",
        content=content,
        metadata=metadata or {},
        start=0,
        end=len(content),
    )


async def save_document(
    *,
    tenant_id: str,
    document_id: str,
    content: str,
) -> None:
    async with AsyncSessionFactory() as session:
        await PostgresDocumentStore(session, tenant_id=tenant_id).save(
            Document(
                id=document_id,
                source="docs/a.md",
                content=content,
                metadata={"version": 1},
            )
        )
        await session.commit()


async def test_postgres_dense_retriever_returns_ordered_hits(fresh_schema) -> None:
    document_id = "doc-1::v1"
    await save_document(
        tenant_id="tenant-a",
        document_id=document_id,
        content="apple banana apple banana",
    )

    chunks = [
        make_chunk(
            chunk_id="chunk-1",
            document_id=document_id,
            content="apple banana",
        ),
        make_chunk(
            chunk_id="chunk-2",
            document_id=document_id,
            content="apple",
        ),
        make_chunk(
            chunk_id="chunk-3",
            document_id=document_id,
            content="banana",
        ),
    ]

    async with AsyncSessionFactory() as session:
        await PostgresChunkStore(session, tenant_id="tenant-a").save_chunks(
            document_id,
            chunks,
            embedder=keyword_embedder,
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        retriever = PostgresDenseRetriever(
            session,
            tenant_id="tenant-a",
            embedder=keyword_embedder,
        )
        hits = await retriever.search("apple banana", top_k=3)

    assert isinstance(retriever, AsyncRetriever)
    assert [hit.chunk.id for hit in hits] == ["chunk-1", "chunk-2", "chunk-3"]
    assert [hit.rank for hit in hits] == [1, 2, 3]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(2**-0.5, rel=1e-6)


async def test_postgres_dense_retriever_filters_tenant_before_top_k(
    fresh_schema,
) -> None:
    await save_document(
        tenant_id="tenant-a",
        document_id="tenant-a-doc::v1",
        content="apple banana",
    )
    await save_document(
        tenant_id="tenant-b",
        document_id="tenant-b-doc::v1",
        content="apple banana",
    )

    async with AsyncSessionFactory() as session:
        await PostgresChunkStore(session, tenant_id="tenant-a").save_chunks(
            "tenant-a-doc::v1",
            [
                make_chunk(
                    chunk_id="a-chunk",
                    document_id="tenant-a-doc::v1",
                    content="apple",
                )
            ],
            embedder=keyword_embedder,
        )
        await PostgresChunkStore(session, tenant_id="tenant-b").save_chunks(
            "tenant-b-doc::v1",
            [
                make_chunk(
                    chunk_id="b-chunk",
                    document_id="tenant-b-doc::v1",
                    content="apple banana",
                )
            ],
            embedder=keyword_embedder,
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        retriever = PostgresDenseRetriever(
            session,
            tenant_id="tenant-a",
            embedder=keyword_embedder,
        )
        hits = await retriever.search("apple banana", top_k=1)

    assert [hit.chunk.id for hit in hits] == ["a-chunk"]


async def test_postgres_dense_retriever_filters_metadata_before_top_k(
    fresh_schema,
) -> None:
    document_id = "doc-1::v1"
    await save_document(
        tenant_id="tenant-a",
        document_id=document_id,
        content="apple banana apple",
    )

    chunks = [
        make_chunk(
            chunk_id="en-chunk",
            document_id=document_id,
            content="apple banana",
            metadata={"lang": "en"},
        ),
        make_chunk(
            chunk_id="zh-chunk",
            document_id=document_id,
            content="apple",
            metadata={"lang": "zh"},
        ),
    ]

    async with AsyncSessionFactory() as session:
        await PostgresChunkStore(session, tenant_id="tenant-a").save_chunks(
            document_id,
            chunks,
            embedder=keyword_embedder,
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        retriever = PostgresDenseRetriever(
            session,
            tenant_id="tenant-a",
            embedder=keyword_embedder,
        )
        hits = await retriever.search(
            "apple banana",
            top_k=1,
            metadata_filter={"lang": "zh"},
        )

    assert [hit.chunk.id for hit in hits] == ["zh-chunk"]


async def test_save_chunks_with_embedder_backfills_null_embedding(
    fresh_schema,
) -> None:
    document_id = "doc-1::v1"
    await save_document(
        tenant_id="tenant-a",
        document_id=document_id,
        content="apple",
    )
    chunk = make_chunk(
        chunk_id="chunk-1",
        document_id=document_id,
        content="apple",
    )

    async with AsyncSessionFactory() as session:
        await PostgresChunkStore(session, tenant_id="tenant-a").save_chunks(
            document_id,
            [chunk],
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        embedding = (
            await session.execute(
                select(RagChunk.embedding).where(
                    RagChunk.tenant_id == "tenant-a",
                    RagChunk.chunk_id == "chunk-1",
                )
            )
        ).scalar_one_or_none()
        assert embedding is None

    async with AsyncSessionFactory() as session:
        await PostgresChunkStore(session, tenant_id="tenant-a").save_chunks(
            document_id,
            [chunk],
            embedder=keyword_embedder,
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        embedding = (
            await session.execute(
                select(RagChunk.embedding).where(
                    RagChunk.tenant_id == "tenant-a",
                    RagChunk.chunk_id == "chunk-1",
                )
            )
        ).scalar_one()
        assert len(embedding) == RAG_EMBEDDING_DIMENSION


async def test_postgres_dense_retriever_ignores_null_embeddings(
    fresh_schema,
) -> None:
    document_id = "doc-1::v1"
    await save_document(
        tenant_id="tenant-a",
        document_id=document_id,
        content="apple banana",
    )

    async with AsyncSessionFactory() as session:
        store = PostgresChunkStore(session, tenant_id="tenant-a")
        await store.save_chunks(
            document_id,
            [
                make_chunk(
                    chunk_id="embedded",
                    document_id=document_id,
                    content="apple",
                )
            ],
            embedder=keyword_embedder,
        )
        await store.save_chunks(
            document_id,
            [
                make_chunk(
                    chunk_id="not-embedded",
                    document_id=document_id,
                    content="banana",
                )
            ],
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        retriever = PostgresDenseRetriever(
            session,
            tenant_id="tenant-a",
            embedder=keyword_embedder,
        )
        hits = await retriever.search("apple", top_k=10)

    assert [hit.chunk.id for hit in hits] == ["embedded"]


async def test_postgres_dense_retriever_rejects_dimension_mismatch(
    fresh_schema,
) -> None:
    async with AsyncSessionFactory() as session:
        retriever = PostgresDenseRetriever(
            session,
            tenant_id="tenant-a",
            embedder=lambda text: [1.0, 0.0],
        )
        with pytest.raises(ValueError, match="dimension"):
            await retriever.search("apple")


def test_keyword_embedder_returns_declared_dimension() -> None:
    vector: Sequence[float] = keyword_embedder("apple banana")
    assert len(vector) == RAG_EMBEDDING_DIMENSION
