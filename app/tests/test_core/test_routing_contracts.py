from collections.abc import Sequence

import pytest

from app.llm.contracts import LLMMessage, LLMProvider, LLMRole
from app.llm.retry import DEFAULT_RETRY_POLICY
from app.llm.routing import ProviderCandidate, ReliabilityPolicy, RoutingStrategy


class FakeProvider:
    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        return "ok"

    async def stream(self, messages: Sequence[LLMMessage]):
        yield "ok"


class FirstStrategy:
    def select(
        self,
        candidates: Sequence[ProviderCandidate],
        messages: Sequence[LLMMessage],
    ) -> ProviderCandidate:
        return candidates[0]


def test_provider_candidate_accepts_valid_provider() -> None:
    provider = FakeProvider()
    candidate = ProviderCandidate(name="primary", provider=provider)

    assert candidate.name == "primary"
    assert isinstance(candidate.provider, LLMProvider)


def test_provider_candidate_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ProviderCandidate(name="", provider=FakeProvider())


def test_provider_candidate_rejects_non_provider() -> None:
    with pytest.raises(TypeError, match="LLMProvider"):
        ProviderCandidate(name="primary", provider=object())  # type: ignore[arg-type]


def test_routing_strategy_is_structurally_satisfied_by_custom_strategy() -> None:
    strategy: RoutingStrategy = FirstStrategy()
    candidates = [ProviderCandidate("primary", FakeProvider())]
    messages = [LLMMessage(role=LLMRole.USER, content="你好")]

    assert isinstance(strategy, RoutingStrategy)
    assert strategy.select(candidates, messages) is candidates[0]


def test_reliability_policy_uses_default_retry_policy() -> None:
    policy = ReliabilityPolicy()

    assert policy.retry_policy == DEFAULT_RETRY_POLICY
    assert policy.max_fallback_attempts == 1


def test_reliability_policy_accepts_zero_fallback_attempts() -> None:
    policy = ReliabilityPolicy(max_fallback_attempts=0)

    assert policy.max_fallback_attempts == 0


def test_reliability_policy_rejects_invalid_retry_policy() -> None:
    with pytest.raises(TypeError, match="RetryPolicy"):
        ReliabilityPolicy(retry_policy=object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [True, -1, 1.5],
)
def test_reliability_policy_rejects_invalid_fallback_attempts(value) -> None:
    with pytest.raises((TypeError, ValueError)):
        ReliabilityPolicy(max_fallback_attempts=value)  # type: ignore[arg-type]
