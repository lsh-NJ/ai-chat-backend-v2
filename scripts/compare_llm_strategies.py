"""本地样例集上的 LLM 策略对比脚本（默认 demo 模式，不请求真实模型）。

用法：
    python scripts/compare_llm_strategies.py

该脚本用两个 demo provider 跑通“质量 + P50/P95 + token + 成本”对比管线。
真实 provider 只要实现 LLMProvider，就可以替换 DemoProvider 接入同一评测逻辑。
"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from app.llm.contracts import LLMMessage, LLMRole
from app.llm.evaluation import EvalCase, evaluate_strategy
from app.llm.observability import TokenUsage

SAMPLE_CASES = [
    EvalCase(
        id="math-1",
        messages=(LLMMessage(role=LLMRole.USER, content="1+1=?"),),
        expected_contains=("2",),
    ),
    EvalCase(
        id="cap-1",
        messages=(LLMMessage(role=LLMRole.USER, content="中国的首都是？"),),
        expected_contains=("北京",),
    ),
    EvalCase(
        id="py-1",
        messages=(LLMMessage(role=LLMRole.USER, content="Python 是编程语言吗？"),),
        expected_contains=("是",),
    ),
    EvalCase(
        id="chem-1",
        messages=(LLMMessage(role=LLMRole.USER, content="水的化学式？"),),
        expected_contains=("H2O",),
    ),
]


class DemoProvider:
    """可替换的 demo provider：只为演示评测管线。"""

    def __init__(self, name: str, answers: dict[str, str], delay: float) -> None:
        self._name = name
        self._answers = answers
        self._delay = delay

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        await asyncio.sleep(self._delay)
        prompt = messages[-1].content
        return self._answers.get(prompt, "我不知道")

    def stream(self, messages: Sequence[LLMMessage]) -> AsyncIterator[str]:
        raise NotImplementedError("demo provider does not implement streaming")


def token_counter(messages: Sequence[LLMMessage], output: str) -> TokenUsage:
    return TokenUsage(input_tokens=len(messages), output_tokens=len(output))


class SimpleCostCalculator:
    def calculate(self, usage: TokenUsage, model: str) -> float:
        return usage.input_tokens * 0.001 + usage.output_tokens * 0.002


async def main() -> None:
    fast = DemoProvider(
        name="fast",
        answers={
            "1+1=?": "1",
            "中国的首都是？": "上海",
            "Python 是编程语言吗？": "不是",
            "水的化学式？": "O2",
        },
        delay=0.01,
    )
    accurate = DemoProvider(
        name="accurate",
        answers={
            "1+1=?": "2",
            "中国的首都是？": "北京",
            "Python 是编程语言吗？": "是",
            "水的化学式？": "H2O",
        },
        delay=0.03,
    )

    reports = [
        await evaluate_strategy(
            "fast-low-quality",
            fast,
            SAMPLE_CASES,
            model="demo-fast",
            token_counter=token_counter,
            cost_calculator=SimpleCostCalculator(),
        ),
        await evaluate_strategy(
            "accurate-slower",
            accurate,
            SAMPLE_CASES,
            model="demo-accurate",
            token_counter=token_counter,
            cost_calculator=SimpleCostCalculator(),
        ),
    ]

    print("策略对比报告（demo）")
    print("=" * 60)
    for report in reports:
        print(f"策略: {report.strategy_name}")
        print(f"  准确率: {report.accuracy:.0%} ({report.passed_cases}/{report.total_cases})")
        print(f"  P50 延迟: {report.p50_latency_seconds:.3f}s")
        print(f"  P95 延迟: {report.p95_latency_seconds:.3f}s")
        print(f"  输入 tokens: {report.total_input_tokens}")
        print(f"  输出 tokens: {report.total_output_tokens}")
        print(f"  估算成本: {report.total_cost:.6f}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
