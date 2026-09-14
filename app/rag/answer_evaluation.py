"""端到端 RAG 答案评测（Week 16 Day 4）。

评测对象是 `RagQueryService` 这一层，不绕过检索、拒答和引用校验。
指标分三类：
- 答案：关键字是否命中，拒答是否符合预期；
- 检索/引用：期望 chunk 是否进入候选、回答引用是否正确；
- 工程：P50/P95 延迟、token、按成本函数估算的成本。

说明：当前 `citation_accuracy` 是引用正确率代理指标，不等于严格
faithfulness。严格 faithfulness 需要 claim -> evidence 的 verifier 或
人工标注，留到后续评测阶段。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from app.llm.evaluation import percentile
from app.llm.observability import CostCalculator, TokenUsage
from app.rag.answer import RagAnswer


class RagEvalCategory(StrEnum):
    """评测问题类型，覆盖 Week 16 计划要求。"""

    ANSWERABLE = "answerable"
    UNANSWERABLE = "unanswerable"
    KEYWORD = "keyword"
    PARAPHRASE = "paraphrase"
    PERMISSION_NEGATIVE = "permission_negative"
    PROMPT_INJECTION = "prompt_injection"


@dataclass(frozen=True, slots=True)
class RagEvalCase:
    """一条人工复核的端到端 RAG 评测样例。"""

    id: str
    question: str
    category: RagEvalCategory
    expected_refused: bool
    expected_answer_contains: tuple[str, ...] = ()
    expected_chunk_ids: tuple[str, ...] = ()
    forbidden_answer_contains: tuple[str, ...] = ()
    reviewed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("case id must be a non-empty string")
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("case question must be a non-empty string")
        if not isinstance(self.category, RagEvalCategory):
            raise TypeError("case category must be a RagEvalCategory")
        if not isinstance(self.expected_refused, bool):
            raise TypeError("expected_refused must be a boolean")
        if not isinstance(self.reviewed, bool):
            raise TypeError("reviewed must be a boolean")
        _validate_string_tuple(
            self.expected_answer_contains,
            "expected_answer_contains",
        )
        _validate_string_tuple(self.expected_chunk_ids, "expected_chunk_ids")
        _validate_string_tuple(
            self.forbidden_answer_contains,
            "forbidden_answer_contains",
        )
        if self.expected_refused and self.expected_answer_contains:
            raise ValueError(
                "refused cases must not declare expected_answer_contains"
            )
        if not self.expected_refused and not self.expected_answer_contains:
            raise ValueError(
                "answerable cases must declare expected_answer_contains"
            )
        if (
            self.category is RagEvalCategory.PERMISSION_NEGATIVE
            and not self.forbidden_answer_contains
        ):
            raise ValueError(
                "permission negative cases must declare forbidden content"
            )


@dataclass(frozen=True, slots=True)
class RagEvalCaseResult:
    """一条样例的评测结果，保留中间判断方便定位 bad case。"""

    case_id: str
    category: RagEvalCategory
    passed: bool
    refusal_correct: bool
    answer_correct: bool
    citation_correct: bool
    retrieval_recall: float
    forbidden_content_found: bool
    latency_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    cost: float | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RagEvalReport:
    """一组端到端样例的汇总报告。"""

    total_cases: int
    passed_cases: int
    accuracy: float
    answer_accuracy: float
    refusal_accuracy: float
    citation_accuracy: float
    mean_retrieval_recall: float
    forbidden_leak_count: int
    p50_latency_seconds: float
    p95_latency_seconds: float
    total_input_tokens: int
    total_output_tokens: int
    total_cost: float
    results: tuple[RagEvalCaseResult, ...]


class RagAnswerer(Protocol):
    """评测只需要一个能回答问题的对象，具体实现由业务组合根提供。"""

    async def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        metadata_filter: Any = None,
    ) -> RagAnswer: ...


def _validate_string_tuple(values: object, field_name: str) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{field_name} must contain non-empty strings")


def _mean(values: Sequence[bool]) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if value) / len(values)


class RagEvaluator:
    """在固定样例集上运行 RAG 服务并汇总指标。"""

    def __init__(
        self,
        answerer: RagAnswerer,
        *,
        top_k: int = 5,
        model: str | None = None,
        cost_calculator: CostCalculator | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        if cost_calculator is not None and not callable(
            getattr(cost_calculator, "calculate", None)
        ):
            raise TypeError("cost_calculator must provide calculate()")
        self._answerer = answerer
        self._top_k = top_k
        self._model = model
        self._cost_calculator = cost_calculator
        self._clock = clock or time.monotonic

    async def evaluate(
        self,
        cases: Sequence[RagEvalCase],
    ) -> RagEvalReport:
        if isinstance(cases, (str, bytes)) or not isinstance(cases, Sequence):
            raise TypeError("cases must be a sequence of RagEvalCase values")
        if not cases:
            raise ValueError("cases must not be empty")

        results: list[RagEvalCaseResult] = []
        for case in cases:
            if not isinstance(case, RagEvalCase):
                raise TypeError("cases must contain RagEvalCase values")
            results.append(await self._evaluate_case(case))

        return self._report(tuple(results))

    async def _evaluate_case(self, case: RagEvalCase) -> RagEvalCaseResult:
        started_at = self._clock()
        error: str | None = None
        try:
            answer = await self._answerer.ask(
                case.question,
                top_k=self._top_k,
            )
        except Exception as exc:  # noqa: BLE001 - 评测必须把异常记为失败
            error = type(exc).__name__
            latency_seconds = self._clock() - started_at
            return RagEvalCaseResult(
                case_id=case.id,
                category=case.category,
                passed=False,
                refusal_correct=False,
                answer_correct=False,
                citation_correct=False,
                retrieval_recall=0.0,
                forbidden_content_found=False,
                latency_seconds=latency_seconds,
                input_tokens=None,
                output_tokens=None,
                cost=None,
                error=error,
            )

        latency_seconds = self._clock() - started_at
        refusal_correct = answer.refused is case.expected_refused
        forbidden_content_found = any(
            term in answer.answer for term in case.forbidden_answer_contains
        )
        answer_correct = self._answer_correct(case, answer)
        citation_correct = self._citation_correct(case, answer)
        retrieval_recall = self._retrieval_recall(case, answer)
        passed = (
            refusal_correct
            and answer_correct
            and citation_correct
            and not forbidden_content_found
        )
        usage = self._token_usage(answer)
        cost = self._cost(usage)
        return RagEvalCaseResult(
            case_id=case.id,
            category=case.category,
            passed=passed,
            refusal_correct=refusal_correct,
            answer_correct=answer_correct,
            citation_correct=citation_correct,
            retrieval_recall=retrieval_recall,
            forbidden_content_found=forbidden_content_found,
            latency_seconds=latency_seconds,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            cost=cost,
            error=error,
        )

    @staticmethod
    def _answer_correct(case: RagEvalCase, answer: RagAnswer) -> bool:
        if case.expected_refused:
            return answer.refused
        if answer.refused:
            return False
        return all(
            term in answer.answer
            for term in case.expected_answer_contains
        )

    @staticmethod
    def _citation_correct(case: RagEvalCase, answer: RagAnswer) -> bool:
        if case.expected_refused:
            return not answer.citations
        if not case.expected_chunk_ids:
            return True
        if not answer.citations:
            return False
        cited_ids = {citation.chunk_id for citation in answer.citations}
        return cited_ids <= set(case.expected_chunk_ids)

    @staticmethod
    def _retrieval_recall(case: RagEvalCase, answer: RagAnswer) -> float:
        if not case.expected_chunk_ids:
            return 1.0
        retrieved = set(answer.retrieved_chunk_ids)
        hit_count = sum(
            1 for chunk_id in case.expected_chunk_ids if chunk_id in retrieved
        )
        return hit_count / len(case.expected_chunk_ids)

    @staticmethod
    def _token_usage(answer: RagAnswer) -> TokenUsage | None:
        if answer.input_tokens is None or answer.output_tokens is None:
            return None
        return TokenUsage(
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
        )

    def _cost(self, usage: TokenUsage | None) -> float | None:
        if (
            usage is None
            or self._cost_calculator is None
            or self._model is None
        ):
            return None
        return self._cost_calculator.calculate(usage, self._model)

    @staticmethod
    def _report(results: tuple[RagEvalCaseResult, ...]) -> RagEvalReport:
        answerable = [
            result
            for result in results
            if result.category is not RagEvalCategory.UNANSWERABLE
            and result.category is not RagEvalCategory.PERMISSION_NEGATIVE
        ]
        refused_cases = [
            result
            for result in results
            if result.category is RagEvalCategory.UNANSWERABLE
            or result.category is RagEvalCategory.PERMISSION_NEGATIVE
        ]
        latencies = [result.latency_seconds for result in results]
        costs = [result.cost for result in results if result.cost is not None]
        return RagEvalReport(
            total_cases=len(results),
            passed_cases=sum(1 for result in results if result.passed),
            accuracy=(
                sum(1 for result in results if result.passed) / len(results)
                if results
                else 0.0
            ),
            answer_accuracy=_mean(
                [result.answer_correct for result in answerable]
            ),
            refusal_accuracy=_mean(
                [result.refusal_correct for result in refused_cases]
            ),
            citation_accuracy=_mean(
                [result.citation_correct for result in answerable]
            ),
            mean_retrieval_recall=(
                sum(result.retrieval_recall for result in results) / len(results)
                if results
                else 0.0
            ),
            forbidden_leak_count=sum(
                1 for result in results if result.forbidden_content_found
            ),
            p50_latency_seconds=percentile(latencies, 50),
            p95_latency_seconds=percentile(latencies, 95),
            total_input_tokens=sum(
                result.input_tokens or 0 for result in results
            ),
            total_output_tokens=sum(
                result.output_tokens or 0 for result in results
            ),
            total_cost=sum(costs),
            results=results,
        )


def load_rag_eval_cases_jsonl(path: str) -> tuple[RagEvalCase, ...]:
    """从 JSONL 读取人工复核样例；空行和以 # 开头的行会被忽略。"""
    eval_path = Path(path)
    cases: list[RagEvalCase] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        eval_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSONL at {eval_path}:{line_number}"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(
                f"eval record must be a JSON object at {eval_path}:{line_number}"
            )
        case = RagEvalCase(
            id=record["id"],
            question=record["question"],
            category=RagEvalCategory(record["category"]),
            expected_refused=record["expected_refused"],
            expected_answer_contains=tuple(
                record.get("expected_answer_contains", [])
            ),
            expected_chunk_ids=tuple(record.get("expected_chunk_ids", [])),
            forbidden_answer_contains=tuple(
                record.get("forbidden_answer_contains", [])
            ),
            reviewed=bool(record.get("reviewed", False)),
        )
        if case.id in seen_ids:
            raise ValueError(f"duplicate eval case id: {case.id}")
        seen_ids.add(case.id)
        cases.append(case)
    if not cases:
        raise ValueError("eval dataset must not be empty")
    return tuple(cases)
