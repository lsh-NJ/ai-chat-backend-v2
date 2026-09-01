from collections.abc import AsyncIterator, Sequence

import pytest

from app.llm.contracts import LLMMessage, LLMRole
from app.llm.evaluation import (
    ComparisonReport,
    EvalCase,
    evaluate_strategy,
    percentile,
)
from app.llm.observability import TokenUsage


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeProvider:
    def __init__(self, outputs, clock, delays) -> None:
        self._outputs = list(outputs)
        self._clock = clock
        self._delays = list(delays)

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        delay = self._delays.pop(0)
        self._clock.advance(delay)
        item = self._outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def stream(self, messages: Sequence[LLMMessage]) -> AsyncIterator[str]:
        raise AssertionError("stream is not used in evaluation")


def _token_counter(messages: Sequence[LLMMessage], output: str) -> TokenUsage:
    return TokenUsage(input_tokens=len(messages), output_tokens=len(output))


class FakeCostCalculator:
    def calculate(self, usage: TokenUsage, model: str) -> float:
        return usage.input_tokens * 0.001 + usage.output_tokens * 0.002


def _case(case_id: str, expected: str) -> EvalCase:
    return EvalCase(
        id=case_id,
        messages=(LLMMessage(role=LLMRole.USER, content="问题"),),
        expected_contains=(expected,),
    )


def test_percentile_calculates_linear_interpolation() -> None:
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)
    assert percentile([], 50) == 0.0
    assert percentile([2], 50) == 2.0


def test_eval_case_validation() -> None:
    with pytest.raises(ValueError, match="id"):
        EvalCase(
            id="",
            messages=(LLMMessage(role=LLMRole.USER, content="问题"),),
            expected_contains=("A",),
        )
    with pytest.raises(ValueError, match="expected_contains"):
        EvalCase(
            id="c1",
            messages=(LLMMessage(role=LLMRole.USER, content="问题"),),
            expected_contains=(),
        )


async def test_evaluate_strategy_computes_report() -> None:
    clock = FakeClock()
    provider = FakeProvider(
        outputs=["A1", "B1", "X", "D1"],
        clock=clock,
        delays=[0.1, 0.2, 0.3, 0.4],
    )
    cases = [
        _case("c1", "A"),
        _case("c2", "B"),
        _case("c3", "C"),
        _case("c4", "D"),
    ]

    report = await evaluate_strategy(
        "deepseek-primary",
        provider,
        cases,
        model="deepseek-v4-flash",
        token_counter=_token_counter,
        cost_calculator=FakeCostCalculator(),
        now=clock,
    )

    assert isinstance(report, ComparisonReport)
    assert report.strategy_name == "deepseek-primary"
    assert report.total_cases == 4
    assert report.passed_cases == 3
    assert report.accuracy == 0.75
    assert report.p50_latency_seconds == pytest.approx(0.25)
    assert report.p95_latency_seconds == pytest.approx(0.385)
    assert report.total_input_tokens == 4
    assert report.total_output_tokens == 7
    assert report.total_cost == pytest.approx(0.018)


async def test_evaluate_strategy_counts_error_as_failure() -> None:
    clock = FakeClock()
    provider = FakeProvider(
        outputs=["A-result"],
        clock=clock,
        delays=[0.1, 0.2],
    )
    # 第一个样例触发异常，第二个样例正常输出并包含期望关键词。
    provider._outputs.insert(0, RuntimeError("boom"))  # noqa: SLF001

    cases = [_case("c1", "A"), _case("c2", "A")]
    report = await evaluate_strategy(
        "mock",
        provider,
        cases,
        model="mock",
        token_counter=_token_counter,
        cost_calculator=FakeCostCalculator(),
        now=clock,
    )

    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.accuracy == 0.5
