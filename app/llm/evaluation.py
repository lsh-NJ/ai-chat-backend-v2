"""本地样例集上的 LLM 策略对比评测。

评测逻辑与真实 provider 解耦：自动化测试使用 fake provider，
真实对比由用户显式运行脚本调用。本模块只负责计算质量、延迟
P50/P95、token 和估算成本。
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.llm.contracts import LLMMessage, LLMProvider
from app.llm.observability import CostCalculator, TokenCounter


@dataclass(frozen=True, slots=True)
class EvalCase:
    """一条本地评测样例。"""

    id: str
    messages: tuple[LLMMessage, ...]
    expected_contains: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("case id must be a non-empty string")
        if not isinstance(self.messages, tuple) or not self.messages:
            raise ValueError("messages must be a non-empty tuple")
        if not isinstance(self.expected_contains, tuple) or not self.expected_contains:
            raise ValueError("expected_contains must be a non-empty tuple")


@dataclass(frozen=True, slots=True)
class EvalResult:
    """单个样例的一次评测结果。"""

    case_id: str
    output: str
    success: bool
    latency_seconds: float
    input_tokens: int
    output_tokens: int
    cost: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    """一套策略在本地样例集上的汇总。"""

    strategy_name: str
    total_cases: int
    passed_cases: int
    accuracy: float
    p50_latency_seconds: float
    p95_latency_seconds: float
    total_input_tokens: int
    total_output_tokens: int
    total_cost: float


def percentile(values: Sequence[float], percent: float) -> float:
    """计算一组延迟的百分位；空列表返回 0。"""

    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (percent / 100)
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return sorted_values[lower] + (
        sorted_values[upper] - sorted_values[lower]
    ) * weight


async def evaluate_strategy(
    strategy_name: str,
    provider: LLMProvider,
    cases: Sequence[EvalCase],
    *,
    model: str,
    token_counter: TokenCounter,
    cost_calculator: CostCalculator,
    now: Callable[[], float] | None = None,
) -> ComparisonReport:
    """用同一套规则评测一个策略，返回汇总报告。"""

    clock = now or time.monotonic
    results: list[EvalResult] = []
    for case in cases:
        started_at = clock()
        try:
            output = await provider.complete(case.messages)
            success = any(
                keyword in output for keyword in case.expected_contains
            )
            error = None
        except Exception as exc:
            output = ""
            success = False
            error = type(exc).__name__

        latency_seconds = clock() - started_at
        usage = token_counter(case.messages, output)
        results.append(
            EvalResult(
                case_id=case.id,
                output=output,
                success=success,
                latency_seconds=latency_seconds,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost=cost_calculator.calculate(usage, model),
                error=error,
            )
        )

    total_cases = len(results)
    passed_cases = sum(1 for result in results if result.success)
    latencies = [result.latency_seconds for result in results]
    return ComparisonReport(
        strategy_name=strategy_name,
        total_cases=total_cases,
        passed_cases=passed_cases,
        accuracy=passed_cases / total_cases if total_cases else 0.0,
        p50_latency_seconds=percentile(latencies, 50),
        p95_latency_seconds=percentile(latencies, 95),
        total_input_tokens=sum(result.input_tokens for result in results),
        total_output_tokens=sum(result.output_tokens for result in results),
        total_cost=sum(result.cost for result in results),
    )
