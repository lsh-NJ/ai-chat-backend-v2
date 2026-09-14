import pytest

from app.core.exceptions import RagCitationError
from app.llm.observability import TokenUsage
from app.llm.tokenization import ContextBudget
from app.rag.chunking import Chunk
from app.rag.context_builder import RagContextBuilder
from app.rag.refusal import (
    REFUSAL_ANSWER,
    RagRefusalPolicy,
    RefusalReason,
)
from app.rag.retrieval import ChunkHit
from app.services.rag_service import RagQueryService
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


async def test_ask_records_token_usage_when_counter_provided() -> None:
    provider = FakeLLMProvider(complete_result="退款需要先提交申请 [1]。")
    service = RagQueryService(
        FakeRetriever([_hit()]),
        provider,
        _builder(),
        token_counter=lambda messages, answer: TokenUsage(
            input_tokens=len(messages),
            output_tokens=len(answer),
        ),
    )

    answer = await service.ask("退款怎么申请？")

    assert answer.input_tokens == 2
    assert answer.output_tokens == len("退款需要先提交申请 [1]。")


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
    assert answer.refusal_reason == RefusalReason.NO_RETRIEVAL_HITS.value
    assert provider.complete_calls == []


async def test_ask_refuses_low_top_score_when_policy_enabled() -> None:
    policy = RagRefusalPolicy(min_top_score=0.5)
    provider = FakeLLMProvider()
    service = RagQueryService(
        FakeRetriever([_hit(score=0.1)]),
        provider,
        _builder(),
        refusal_policy=policy,
    )

    answer = await service.ask("退款怎么申请？")

    assert answer.refused is True
    assert answer.refusal_reason == RefusalReason.LOW_EVIDENCE_SCORE.value
    assert provider.complete_calls == []


async def test_ask_normalizes_model_refusal() -> None:
    provider = FakeLLMProvider(complete_result=REFUSAL_ANSWER)
    service = RagQueryService(FakeRetriever([_hit()]), provider, _builder())

    answer = await service.ask("退款怎么申请？")

    assert answer.refused is True
    assert answer.answer == REFUSAL_ANSWER
    assert answer.citations == ()
    assert answer.refusal_reason == RefusalReason.MODEL_REFUSED.value
    assert len(provider.complete_calls) == 1


async def test_ask_refuses_uncited_non_refusal_answer() -> None:
    provider = FakeLLMProvider(complete_result="退款需要先提交申请。")
    service = RagQueryService(FakeRetriever([_hit()]), provider, _builder())

    answer = await service.ask("退款怎么申请？")

    assert answer.refused is True
    assert answer.answer == REFUSAL_ANSWER
    assert answer.citations == ()
    assert answer.refusal_reason == RefusalReason.MISSING_CITATION.value


async def test_ask_allows_uncited_answer_when_policy_disables_citation_rule() -> None:
    policy = RagRefusalPolicy(require_citation=False)
    provider = FakeLLMProvider(complete_result="退款需要先提交申请。")
    service = RagQueryService(
        FakeRetriever([_hit()]),
        provider,
        _builder(),
        refusal_policy=policy,
    )

    answer = await service.ask("退款怎么申请？")

    assert answer.refused is False
    assert answer.answer == "退款需要先提交申请。"
    assert answer.citations == ()
