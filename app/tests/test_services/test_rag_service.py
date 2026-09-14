import pytest

from app.core.exceptions import RagCitationError
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


def _hit(
    *,
    chunk_id: str = "refund-steps",
    content: str = "退款需要先提交申请，审核通过后退款到账。",
    score: float = 0.9,
    rank: int = 1,
) -> ChunkHit:
    return ChunkHit(
        chunk=Chunk(
            id=chunk_id,
            document_id="doc-refund",
            source="docs/refund.md",
            content=content,
            metadata={"lang": "zh"},
            start=0,
            end=len(content),
        ),
        score=score,
        rank=rank,
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


async def test_ask_returns_only_citations_used_by_answer() -> None:
    first = _hit(
        chunk_id="refund-policy",
        content="退款政策：七天内可以申请。",
        score=0.9,
        rank=1,
    )
    second = _hit(
        chunk_id="refund-steps",
        content="退款流程：先提交申请再审核。",
        score=0.8,
        rank=2,
    )
    provider = FakeLLMProvider(complete_result="先提交申请即可 [2]。")
    service = RagQueryService(FakeRetriever([first, second]), provider, _builder())

    answer = await service.ask("退款怎么申请？")

    assert [citation.chunk_id for citation in answer.citations] == [
        "refund-steps"
    ]
    assert answer.retrieved_chunk_ids == ("refund-policy", "refund-steps")


async def test_ask_rejects_unknown_citation_label() -> None:
    provider = FakeLLMProvider(complete_result="依据 [9] 可以回答。")
    service = RagQueryService(FakeRetriever([_hit()]), provider, _builder())

    with pytest.raises(RagCitationError, match=r"\[9\]"):
        await service.ask("退款怎么申请？")


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
