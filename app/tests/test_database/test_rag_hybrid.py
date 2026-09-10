"""Week 15 Day 4：Hybrid + RRF 真库集成验收。"""

from collections.abc import Sequence

from app.db.session import AsyncSessionFactory
from app.models.rag import RAG_EMBEDDING_DIMENSION
from app.rag.chunking import Chunk
from app.rag.documents import Document
from app.rag.hybrid import AsyncHybridRetriever
from app.rag.postgres_dense import PostgresDenseRetriever
from app.rag.postgres_fts import PostgresFullTextRetriever
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


async def save_chunks(
    *,
    tenant_id: str,
    document_id: str,
    chunks: Sequence[Chunk],
    embedder=keyword_embedder,
) -> None:
    async with AsyncSessionFactory() as session:
        await PostgresChunkStore(
            session,
            tenant_id=tenant_id,
        ).save_chunks(document_id, chunks, embedder=embedder)
        await session.commit()


async def test_hybrid_combines_real_fts_and_dense(fresh_schema) -> None:
    document_id = "doc-1::v1"
    await save_document(
        tenant_id="tenant-a",
        document_id=document_id,
        content="apple banana apple banana",
    )
    await save_chunks(
        tenant_id="tenant-a",
        document_id=document_id,
        chunks=[
            make_chunk(
                chunk_id="both",
                document_id=document_id,
                content="apple banana",
            ),
            make_chunk(
                chunk_id="apple-only",
                document_id=document_id,
                content="apple apple",
            ),
            make_chunk(
                chunk_id="banana-only",
                document_id=document_id,
                content="banana banana",
            ),
        ],
    )

    async with AsyncSessionFactory() as session:
        hybrid = AsyncHybridRetriever(
            PostgresFullTextRetriever(session, tenant_id="tenant-a"),
            PostgresDenseRetriever(
                session,
                tenant_id="tenant-a",
                embedder=keyword_embedder,
            ),
            candidate_multiplier=4,
        )
        hits = await hybrid.search("apple banana", top_k=3)

    assert isinstance(hybrid, AsyncRetriever)
    assert [hit.chunk.id for hit in hits] == ["both", "apple-only", "banana-only"]
    assert [hit.rank for hit in hits] == [1, 2, 3]


async def test_hybrid_keeps_sparse_hit_when_dense_branch_is_empty(
    fresh_schema,
) -> None:
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
                chunk_id="no-embedding",
                document_id=document_id,
                content="apple",
            )
        ],
        embedder=None,
    )

    async with AsyncSessionFactory() as session:
        hybrid = AsyncHybridRetriever(
            PostgresFullTextRetriever(session, tenant_id="tenant-a"),
            PostgresDenseRetriever(
                session,
                tenant_id="tenant-a",
                embedder=keyword_embedder,
            ),
        )
        hits = await hybrid.search("apple", top_k=3)

    assert [hit.chunk.id for hit in hits] == ["no-embedding"]


async def test_hybrid_applies_metadata_filter_to_both_branches(
    fresh_schema,
) -> None:
    document_id = "doc-1::v1"
    await save_document(
        tenant_id="tenant-a",
        document_id=document_id,
        content="apple banana apple banana",
    )
    await save_chunks(
        tenant_id="tenant-a",
        document_id=document_id,
        chunks=[
            make_chunk(
                chunk_id="en-chunk",
                document_id=document_id,
                content="apple banana",
                metadata={"lang": "en"},
            ),
            make_chunk(
                chunk_id="zh-chunk",
                document_id=document_id,
                content="apple banana",
                metadata={"lang": "zh"},
            ),
        ],
    )

    async with AsyncSessionFactory() as session:
        hybrid = AsyncHybridRetriever(
            PostgresFullTextRetriever(session, tenant_id="tenant-a"),
            PostgresDenseRetriever(
                session,
                tenant_id="tenant-a",
                embedder=keyword_embedder,
            ),
        )
        hits = await hybrid.search(
            "apple banana",
            top_k=1,
            metadata_filter={"lang": "zh"},
        )

    assert [hit.chunk.id for hit in hits] == ["zh-chunk"]
