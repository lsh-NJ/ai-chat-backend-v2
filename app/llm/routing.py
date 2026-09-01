"""Provider-neutral 的路由与可靠性契约。

本模块定义“候选 provider”和“路由策略”的形状，不包含 HTTP、
具体供应商或业务 service 依赖。Fallback、rate limit、circuit breaker
等控制面在后续 Day 中基于这些契约实现。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.llm.contracts import LLMMessage, LLMProvider
from app.llm.retry import DEFAULT_RETRY_POLICY, RetryPolicy


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    """一个可被路由选择的后端模型候选。"""

    name: str
    provider: LLMProvider

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("candidate name must be a non-empty string")
        if not isinstance(self.provider, LLMProvider):
            raise TypeError("candidate provider must satisfy LLMProvider")


@runtime_checkable
class RoutingStrategy(Protocol):
    """从候选 provider 中选出一个用于本次调用的策略。"""

    def select(
        self,
        candidates: Sequence[ProviderCandidate],
        messages: Sequence[LLMMessage],
    ) -> ProviderCandidate:
        """返回选中的候选；策略内部不得调用模型。"""
        ...


class FirstCandidateStrategy:
    """最简单的确定性路由：总是选择候选列表中的第一个。"""

    def select(
        self,
        candidates: Sequence[ProviderCandidate],
        messages: Sequence[LLMMessage],
    ) -> ProviderCandidate:
        if not candidates:
            raise ValueError("at least one candidate is required")
        return candidates[0]


@dataclass(frozen=True, slots=True)
class ReliabilityPolicy:
    """组合 retry 与 fallback 上限的应用层可靠性配置。

    ``max_fallback_attempts`` 表示主 provider 重试仍失败后，
    最多继续尝试几个备用候选；0 表示关闭 fallback。
    """

    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY
    max_fallback_attempts: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.retry_policy, RetryPolicy):
            raise TypeError("retry_policy must be RetryPolicy")
        if isinstance(self.max_fallback_attempts, bool) or not isinstance(
            self.max_fallback_attempts, int
        ):
            raise TypeError("max_fallback_attempts must be an integer")
        if self.max_fallback_attempts < 0:
            raise ValueError("max_fallback_attempts must not be negative")
