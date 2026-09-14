from contextlib import asynccontextmanager

import httpx

from app.core.security import create_access_token
from app.db.session import AsyncSessionFactory, get_db
from app.llm.context import ContextSelector
from app.llm.tokenization import ContextBudget
from app.main import create_app
from app.rag.chunking import Chunk
from app.rag.dense import hash_embed
from app.rag.documents import Document
from app.rag.postgres_store import PostgresChunkStore, PostgresDocumentStore
from app.rag.retrieval import ChunkHit
from app.tests.fakes import (
    ContentLengthTokenCounter,
    FakeLLMProvider,
    FakeRetriever,
)


def _chunk(
    *,
    chunk_id: str,
    document_id: str,
    source: str,
    content: str,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id=document_id,
        source=source,
        content=content,
        metadata={"lang": "zh"},
        start=0,
        end=len(content),
    )


def _hit() -> ChunkHit:
    content = "退款需要先提交申请，审核通过后退款到账。"
    return ChunkHit(
        chunk=Chunk(
            id="refund-steps",
            document_id="doc-refund",
            source="docs/refund.md",
            content=content,
            metadata={"lang": "zh"},
            start=0,
            end=len(content),
        ),
        score=0.9,
        rank=1,
    )


def _selector() -> ContextSelector:
    return ContextSelector(
        ContentLengthTokenCounter(),
        ContextBudget(context_window=10_000, output_reserve=1),
    )


async def _seed_chunk(
    *,
    tenant_id: str,
    document_id: str,
    chunk_id: str,
    content: str,
) -> None:
    source = f"docs/{document_id}.md"
    async with AsyncSessionFactory() as session:
        await PostgresDocumentStore(session, tenant_id=tenant_id).save(
            Document(
                id=document_id,
                source=source,
                content=content,
                metadata={"version": 1},
            )
        )
        await PostgresChunkStore(session, tenant_id=tenant_id).save_chunks(
            document_id,
            [
                _chunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    source=source,
                    content=content,
                )
            ],
            embedder=hash_embed,
        )
        await session.commit()


@asynccontextmanager
async def _client_with_retriever(retriever, provider):
    test_app = create_app(
        llm_provider=provider,
        context_selector=_selector(),
        rag_retriever_factory=lambda session, tenant_id: retriever,
    )

    async def override_get_db():
        async with AsyncSessionFactory() as session:
            yield session

    test_app.dependency_overrides[get_db] = override_get_db
    async with test_app.router.lifespan_context(test_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            yield client
    test_app.dependency_overrides.clear()


async def test_rag_query_requires_authentication(client) -> None:
    response = await client.post(
        "/rag/query",
        json={"question": "退款怎么申请？"},
    )

    assert response.status_code == 401


async def test_rag_query_rejects_client_supplied_tenant_id(
    client,
    auth_headers,
) -> None:
    response = await client.post(
        "/rag/query",
        json={
            "question": "退款怎么申请？",
            "tenant_id": "attacker-controlled",
        },
        headers=auth_headers,
    )

    assert response.status_code == 422


async def test_rag_query_returns_answer_and_citations(
    fresh_schema,
    create_test_user,
) -> None:
    user = await create_test_user("rag-owner")
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    retriever = FakeRetriever([_hit()])
    provider = FakeLLMProvider(complete_result="退款需要先提交申请 [1]。")

    async with _client_with_retriever(retriever, provider) as client:
        response = await client.post(
            "/rag/query",
            json={"question": "退款怎么申请？", "top_k": 3},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["refused"] is False
    assert body["answer"] == "退款需要先提交申请 [1]。"
    assert body["retrieved_chunk_ids"] == ["refund-steps"]
    assert body["citations"] == [
        {
            "label": "1",
            "chunk_id": "refund-steps",
            "document_id": "doc-refund",
            "source": "docs/refund.md",
            "start": 0,
            "end": len("退款需要先提交申请，审核通过后退款到账。"),
            "score": 0.9,
        }
    ]
    assert retriever.calls == [("退款怎么申请？", 3, None)]


async def test_rag_query_refuses_when_no_evidence(
    fresh_schema,
    create_test_user,
) -> None:
    user = await create_test_user("rag-no-evidence")
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    provider = FakeLLMProvider()

    async with _client_with_retriever(FakeRetriever([]), provider) as client:
        response = await client.post(
            "/rag/query",
            json={"question": "不存在的制度"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["refused"] is True
    assert body["refusal_reason"] == "no_retrieval_hits"
    assert body["citations"] == []
    assert provider.complete_calls == []


async def test_rag_query_invalid_citation_returns_502(
    fresh_schema,
    create_test_user,
) -> None:
    user = await create_test_user("rag-invalid-citation")
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    provider = FakeLLMProvider(complete_result="依据 [99] 可以回答。")

    async with _client_with_retriever(FakeRetriever([_hit()]), provider) as client:
        response = await client.post(
            "/rag/query",
            json={"question": "退款怎么申请？"},
            headers=headers,
        )

    assert response.status_code == 502
    assert "[99]" in response.json()["detail"]


async def test_rag_query_uses_real_postgres_and_isolates_tenant(
    client,
    llm_provider,
    create_test_user,
) -> None:
    user = await create_test_user("rag-real-postgres")
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    own_tenant = f"user:{user.id}"

    await _seed_chunk(
        tenant_id=own_tenant,
        document_id="refund-doc::v1",
        chunk_id="refund-own",
        content="退款需要先提交申请，审核通过后退款到账。",
    )
    await _seed_chunk(
        tenant_id="user:999999",
        document_id="refund-secret::v1",
        chunk_id="refund-secret",
        content="其他租户的秘密退款流程：先联系董事长。",
    )

    llm_provider.complete_result = "退款需要先提交申请 [1]。"
    response = await client.post(
        "/rag/query",
        json={"question": "退款怎么申请？", "top_k": 5},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["refused"] is False
    assert [citation["chunk_id"] for citation in body["citations"]] == [
        "refund-own"
    ]

    assert len(llm_provider.complete_calls) == 1
    sent_prompt = llm_provider.complete_calls[0][1].content
    assert "退款需要先提交申请" in sent_prompt
    assert "其他租户的秘密退款流程" not in sent_prompt


async def test_rag_query_refuses_when_only_other_tenant_has_evidence(
    client,
    llm_provider,
    create_test_user,
) -> None:
    user = await create_test_user("rag-permission-negative")
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}

    await _seed_chunk(
        tenant_id="user:888888",
        document_id="secret-refund::v1",
        chunk_id="secret-refund",
        content="另一个租户的秘密退款流程：先联系董事长。",
    )

    response = await client.post(
        "/rag/query",
        json={"question": "退款怎么申请？"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["refused"] is True
    assert body["refusal_reason"] == "no_retrieval_hits"
    assert body["citations"] == []
    assert llm_provider.complete_calls == []
