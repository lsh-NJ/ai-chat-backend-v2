from pathlib import Path

import pytest

from app.llm.observability import TokenUsage
from app.rag.answer import RagAnswer, RagCitation
from app.rag.answer_evaluation import (
    RagEvalCase,
    RagEvalCategory,
    RagEvaluator,
    load_rag_eval_cases_jsonl,
)
from app.rag.refusal import REFUSAL_ANSWER


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeAnswerer:
    def __init__(self, answers, clock) -> None:
        self._answers = list(answers)
        self._clock = clock

    async def ask(self, question, *, top_k=5, metadata_filter=None) -> RagAnswer:
        self._clock.advance(0.1)
        item = self._answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeCostCalculator:
    def calculate(self, usage: TokenUsage, model: str) -> float:
        return usage.input_tokens * 0.001 + usage.output_tokens * 0.002


def _citation(label: str, chunk_id: str) -> RagCitation:
    return RagCitation(
        label=label,
        chunk_id=chunk_id,
        document_id="doc-1",
        source="docs/faq.md",
        start=0,
        end=4,
        score=1.0,
    )


def _case(
    case_id: str,
    *,
    category: RagEvalCategory,
    expected_refused: bool,
    expected_answer_contains: tuple[str, ...] = (),
    expected_chunk_ids: tuple[str, ...] = (),
    forbidden_answer_contains: tuple[str, ...] = (),
) -> RagEvalCase:
    return RagEvalCase(
        id=case_id,
        question="问题",
        category=category,
        expected_refused=expected_refused,
        expected_answer_contains=expected_answer_contains,
        expected_chunk_ids=expected_chunk_ids,
        forbidden_answer_contains=forbidden_answer_contains,
    )


def test_eval_case_validation() -> None:
    with pytest.raises(ValueError, match="refused cases"):
        _case(
            "bad-refusal",
            category=RagEvalCategory.UNANSWERABLE,
            expected_refused=True,
            expected_answer_contains=("不应有",),
        )

    with pytest.raises(ValueError, match="forbidden content"):
        _case(
            "bad-permission",
            category=RagEvalCategory.PERMISSION_NEGATIVE,
            expected_refused=True,
        )


async def test_evaluator_computes_answer_refusal_and_citation_metrics() -> None:
    clock = FakeClock()
    answerer = FakeAnswerer(
        [
            RagAnswer(
                answer="先提交申请 [1]。",
                citations=(_citation("1", "chunk-a"),),
                retrieved_chunk_ids=("chunk-a",),
                input_tokens=10,
                output_tokens=5,
            ),
            RagAnswer(
                answer=REFUSAL_ANSWER,
                citations=(),
                retrieved_chunk_ids=(),
                refused=True,
                refusal_reason="no_retrieval_hits",
            ),
        ],
        clock,
    )
    cases = [
        _case(
            "answerable",
            category=RagEvalCategory.ANSWERABLE,
            expected_refused=False,
            expected_answer_contains=("提交申请",),
            expected_chunk_ids=("chunk-a",),
        ),
        _case(
            "unanswerable",
            category=RagEvalCategory.UNANSWERABLE,
            expected_refused=True,
        ),
    ]
    evaluator = RagEvaluator(
        answerer,
        model="test-model",
        cost_calculator=FakeCostCalculator(),
        clock=clock,
    )

    report = await evaluator.evaluate(cases)

    assert report.total_cases == 2
    assert report.passed_cases == 2
    assert report.accuracy == 1.0
    assert report.answer_accuracy == 1.0
    assert report.refusal_accuracy == 1.0
    assert report.citation_accuracy == 1.0
    assert report.mean_retrieval_recall == 1.0
    assert report.forbidden_leak_count == 0
    assert report.p50_latency_seconds == pytest.approx(0.1)
    assert report.total_input_tokens == 10
    assert report.total_output_tokens == 5
    assert report.total_cost == pytest.approx(0.02)


async def test_evaluator_marks_forbidden_content_as_leak() -> None:
    clock = FakeClock()
    answerer = FakeAnswerer(
        [
            RagAnswer(
                answer="另一个租户的秘密流程。",
                citations=(),
                retrieved_chunk_ids=(),
                refused=False,
            )
        ],
        clock,
    )
    cases = [
        _case(
            "permission-negative",
            category=RagEvalCategory.PERMISSION_NEGATIVE,
            expected_refused=True,
            forbidden_answer_contains=("秘密",),
        )
    ]

    report = await RagEvaluator(answerer, clock=clock).evaluate(cases)

    assert report.forbidden_leak_count == 1
    assert report.passed_cases == 0
    assert report.results[0].forbidden_content_found is True


async def test_evaluator_marks_exception_as_failure() -> None:
    clock = FakeClock()
    answerer = FakeAnswerer([RuntimeError("boom")], clock)
    cases = [
        _case(
            "answerable",
            category=RagEvalCategory.ANSWERABLE,
            expected_refused=False,
            expected_answer_contains=("答案",),
        )
    ]

    report = await RagEvaluator(answerer, clock=clock).evaluate(cases)

    assert report.passed_cases == 0
    assert report.results[0].error == "RuntimeError"


def test_load_seed_dataset() -> None:
    data_path = (
        Path(__file__).resolve().parents[3]
        / "eval_data"
        / "rag_eval_seed.jsonl"
    )

    cases = load_rag_eval_cases_jsonl(str(data_path))

    assert len(cases) == 12
    categories = {case.category for case in cases}
    assert RagEvalCategory.ANSWERABLE in categories
    assert RagEvalCategory.UNANSWERABLE in categories
    assert RagEvalCategory.PERMISSION_NEGATIVE in categories
    assert RagEvalCategory.PROMPT_INJECTION in categories


def test_load_generated_dataset_has_80_plus_cases() -> None:
    data_path = (
        Path(__file__).resolve().parents[3]
        / "eval_data"
        / "rag_eval_cases.jsonl"
    )

    cases = load_rag_eval_cases_jsonl(str(data_path))

    assert len(cases) >= 80
    assert all(isinstance(case.reviewed, bool) for case in cases)
    categories = {case.category for case in cases}
    assert RagEvalCategory.ANSWERABLE in categories
    assert RagEvalCategory.UNANSWERABLE in categories
    assert RagEvalCategory.PERMISSION_NEGATIVE in categories
    retrieval_cases = [case for case in cases if case.expected_chunk_ids]
    assert len(retrieval_cases) >= 80
