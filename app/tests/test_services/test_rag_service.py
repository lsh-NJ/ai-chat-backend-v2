from app.llm.tokenization import ContextBudget
from app.rag.chunking import Chunk
from app.rag.context_builder import RagContextBuilder
from app.rag.retrieval import ChunkHit
from app.services.rag_service import REFUSAL_ANSWER, RagQueryService
from app.tests.fakes import (
    ContentLengthTokenCounter,
    FakeLLMProvider,
    FakeRetriever,
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


def _builder() -> RagContextBuilder:
    return RagContextBuilder(
        ContentLengthTokenCounter(),
        ContextBudget(context_window=10_000, output_reserve=1),
    )


async def test_ask_returns_answer_with_citations() -> None:
    retriever = FakeRetriever([_hit()])
    provider = FakeLLMProvider(complete_result="退款需要先提交申请 [1]。")
    service = RagQueryService(retriever, provider, _builder())

    answer = await service.ask("退款怎么申请？", top_k=3)

    assert answer.refused is False
    assert answer.answer == "退款需要先提交申请 [1]。"
    assert [citation.chunk_id for citation in answer.citations] == [
        "refund-steps"
    ]
    assert answer.retrieved_chunk_ids == ("refund-steps",)
    assert retriever.calls == [("退款怎么申请？", 3, None)]
    assert len(provider.complete_calls) == 1
    assert "[1] 来源: docs/refund.md" in provider.complete_calls[0][1].content


async def test_ask_refuses_when_retriever_returns_no_hits() -> None:
    retriever = FakeRetriever([])
    provider = FakeLLMProvider()
    service = RagQueryService(retriever, provider, _builder())

    answer = await service.ask("退款怎么申请？")

    assert answer.refused is True
    assert answer.answer == REFUSAL_ANSWER
    assert answer.citations == ()
    assert answer.retrieved_chunk_ids == ()
    assert provider.complete_calls == []
