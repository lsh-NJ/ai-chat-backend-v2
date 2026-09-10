"""Week 15 Day 4：PostgreSQL 全文检索器真库验收。"""

from collections.abc import Sequence

from app.db.session import AsyncSessionFactory
from app.rag.chunking import Chunk
from app.rag.documents import Document
from app.rag.postgres_fts import PostgresFullTextRetriever
from app.rag.postgres_store import PostgresChunkStore, PostgresDocumentStore
from app.rag.retrieval import AsyncRetriever


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


async def save_chunks(
    *,
    tenant_id: str,
    document_id: str,
    chunks: Sequence[Chunk],
) -> None:
    async with AsyncSessionFactory() as session:
        await PostgresChunkStore(
            session,
            tenant_id=tenant_id,
        ).save_chunks(document_id, chunks)
        await session.commit()


async def test_postgres_fts_returns_matching_hits_ordered(fresh_schema) -> None:
    document_id = "doc-1::v1"
    await save_document(
        tenant_id="tenant-a",
        document_id=document_id,
        content="apple apple apple apple banana",
    )
    await save_chunks(
        tenant_id="tenant-a",
        document_id=document_id,
        chunks=[
            make_chunk(
                chunk_id="many-apple",
                document_id=document_id,
                content="apple apple apple",
            ),
            make_chunk(
                chunk_id="one-apple",
                document_id=document_id,
                content="apple banana",
            ),
            make_chunk(
                chunk_id="banana-only",
                document_id=document_id,
                content="banana banana",
            ),
        ],
    )

    async with AsyncSessionFactory() as session:
        retriever = PostgresFullTextRetriever(session, tenant_id="tenant-a")
        hits = await retriever.search("apple", top_k=10)

    assert isinstance(retriever, AsyncRetriever)
    assert [hit.chunk.id for hit in hits] == ["many-apple", "one-apple"]
    assert [hit.rank for hit in hits] == [1, 2]
    assert hits[0].score >= hits[1].score


async def test_postgres_fts_filters_tenant_before_top_k(fresh_schema) -> None:
    await save_document(
        tenant_id="tenant-a",
        document_id="a-doc::v1",
        content="apple",
    )
    await save_document(
        tenant_id="tenant-b",
        document_id="b-doc::v1",
        content="apple apple apple",
    )
    await save_chunks(
        tenant_id="tenant-a",
        document_id="a-doc::v1",
        chunks=[
            make_chunk(
                chunk_id="a-chunk",
                document_id="a-doc::v1",
                content="apple",
            )
        ],
    )
    await save_chunks(
        tenant_id="tenant-b",
        document_id="b-doc::v1",
        chunks=[
            make_chunk(
                chunk_id="b-chunk",
                document_id="b-doc::v1",
                content="apple apple apple",
            )
        ],
    )

    async with AsyncSessionFactory() as session:
        retriever = PostgresFullTextRetriever(session, tenant_id="tenant-a")
        hits = await retriever.search("apple", top_k=1)

    assert [hit.chunk.id for hit in hits] == ["a-chunk"]


async def test_postgres_fts_filters_metadata_before_top_k(fresh_schema) -> None:
    document_id = "doc-1::v1"
    await save_document(
        tenant_id="tenant-a",
        document_id=document_id,
        content="apple apple apple",
    )
    await save_chunks(
        tenant_id="tenant-a",
        document_id=document_id,
        chunks=[
            make_chunk(
                chunk_id="en-chunk",
                document_id=document_id,
                content="apple apple apple",
                metadata={"lang": "en"},
            ),
            make_chunk(
                chunk_id="zh-chunk",
                document_id=document_id,
                content="apple",
                metadata={"lang": "zh"},
            ),
        ],
    )

    async with AsyncSessionFactory() as session:
        retriever = PostgresFullTextRetriever(session, tenant_id="tenant-a")
        hits = await retriever.search(
            "apple",
            top_k=1,
            metadata_filter={"lang": "zh"},
        )

    assert [hit.chunk.id for hit in hits] == ["zh-chunk"]


async def test_postgres_fts_returns_empty_when_no_match(fresh_schema) -> None:
    document_id = "doc-1::v1"
    await save_document(
        tenant_id="tenant-a",
        document_id=document_id,
        content="apple banana",
    )
    await save_chunks(
        tenant_id="tenant-a",
        document_id=document_id,
        chunks=[
            make_chunk(
                chunk_id="chunk-1",
                document_id=document_id,
                content="apple banana",
            )
        ],
    )

    async with AsyncSessionFactory() as session:
        retriever = PostgresFullTextRetriever(session, tenant_id="tenant-a")
        hits = await retriever.search("nonexistent", top_k=10)

    assert hits == ()
