from collections.abc import AsyncIterator, Sequence

import pytest

from app.llm.contracts import LLMMessage, LLMRole
from app.llm.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    FixedWindowRateLimiter,
    RateLimitExceededError,
    ResilientLLMProvider,
)

MESSAGES = [LLMMessage(role=LLMRole.USER, content="你好")]


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeProvider:
    def __init__(
        self,
        complete_results=(),
        stream_sequences=(),
    ) -> None:
        self._complete_results = list(complete_results)
        self._stream_sequences = list(stream_sequences)
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        self.complete_calls += 1
        item = self._complete_results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def stream(self, messages: Sequence[LLMMessage]) -> AsyncIterator[str]:
        self.stream_calls += 1
        sequence = self._stream_sequences.pop(0)

        async def generate() -> AsyncIterator[str]:
            for item in sequence:
                if isinstance(item, Exception):
                    raise item
                yield item

        return generate()


# ---------- Rate Limiter ----------


def test_rate_limiter_allows_up_to_max_then_denies() -> None:
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(
        max_requests=2,
        window_seconds=10,
        now=clock,
    )

    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False


def test_rate_limiter_resets_after_window() -> None:
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(
        max_requests=1,
        window_seconds=10,
        now=clock,
    )

    assert limiter.allow() is True
    assert limiter.allow() is False

    clock.advance(10)
    assert limiter.allow() is True


@pytest.mark.parametrize(
    ("max_requests", "window_seconds", "error_type"),
    [
        (0, 1.0, ValueError),
        (True, 1.0, TypeError),
        (1, 0, ValueError),
        (1, True, TypeError),
    ],
)
def test_rate_limiter_rejects_invalid_config(
    max_requests,
    window_seconds,
    error_type,
) -> None:
    with pytest.raises(error_type):
        FixedWindowRateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
        )


# ---------- Circuit Breaker ----------


def test_circuit_breaker_opens_after_threshold() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=10,
        now=clock,
    )

    breaker.before_call()
    breaker.record_failure()
    breaker.before_call()
    breaker.record_failure()

    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_circuit_breaker_transitions_to_half_open_after_timeout() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=10,
        now=clock,
    )

    breaker.before_call()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN

    clock.advance(10)
    breaker.before_call()
    assert breaker.state is CircuitState.HALF_OPEN


def test_circuit_breaker_half_open_success_closes() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=10,
        now=clock,
    )

    breaker.before_call()
    breaker.record_failure()
    clock.advance(10)
    breaker.before_call()

    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    breaker.before_call()  # closed 后可以正常调用


def test_circuit_breaker_half_open_failure_reopens() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=10,
        now=clock,
    )

    breaker.before_call()
    breaker.record_failure()
    clock.advance(10)
    breaker.before_call()
    breaker.record_failure()

    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_circuit_breaker_half_open_budget_limits_trials() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=10,
        half_open_max_calls=1,
        now=clock,
    )

    breaker.before_call()
    breaker.record_failure()
    clock.advance(10)
    breaker.before_call()

    with pytest.raises(CircuitOpenError):
        breaker.before_call()


@pytest.mark.parametrize(
    ("failure_threshold", "recovery_timeout_seconds", "error_type"),
    [
        (0, 1.0, ValueError),
        (True, 1.0, TypeError),
        (1, 0, ValueError),
        (1, True, TypeError),
    ],
)
def test_circuit_breaker_rejects_invalid_config(
    failure_threshold,
    recovery_timeout_seconds,
    error_type,
) -> None:
    with pytest.raises(error_type):
        CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_timeout_seconds,
        )


def test_circuit_breaker_rejects_invalid_half_open_max_calls() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_seconds=1.0,
            half_open_max_calls=0,
        )


# ---------- Resilient Provider ----------


async def test_complete_rate_limited_does_not_call_provider() -> None:
    clock = FakeClock()
    provider = FakeProvider(complete_results=["ok"])
    resilient = ResilientLLMProvider(
        provider=provider,
        rate_limiter=FixedWindowRateLimiter(1, 10, now=clock),
        circuit_breaker=CircuitBreaker(2, 10, now=clock),
    )

    assert await resilient.complete(MESSAGES) == "ok"
    with pytest.raises(RateLimitExceededError):
        await resilient.complete(MESSAGES)

    assert provider.complete_calls == 1


async def test_complete_circuit_open_does_not_call_provider() -> None:
    clock = FakeClock()
    provider = FakeProvider(complete_results=[RuntimeError("boom")])
    resilient = ResilientLLMProvider(
        provider=provider,
        rate_limiter=FixedWindowRateLimiter(10, 10, now=clock),
        circuit_breaker=CircuitBreaker(1, 10, now=clock),
    )

    with pytest.raises(RuntimeError):
        await resilient.complete(MESSAGES)
    with pytest.raises(CircuitOpenError):
        await resilient.complete(MESSAGES)

    assert provider.complete_calls == 1


async def test_complete_success_records_success() -> None:
    clock = FakeClock()
    provider = FakeProvider(complete_results=["ok"])
    breaker = CircuitBreaker(2, 10, now=clock)
    resilient = ResilientLLMProvider(
        provider=provider,
        rate_limiter=FixedWindowRateLimiter(10, 10, now=clock),
        circuit_breaker=breaker,
    )

    await resilient.complete(MESSAGES)
    assert breaker.state is CircuitState.CLOSED


async def test_stream_success_records_success() -> None:
    clock = FakeClock()
    provider = FakeProvider(stream_sequences=[["a", "b"]])
    breaker = CircuitBreaker(2, 10, now=clock)
    resilient = ResilientLLMProvider(
        provider=provider,
        rate_limiter=FixedWindowRateLimiter(10, 10, now=clock),
        circuit_breaker=breaker,
    )

    chunks = [chunk async for chunk in resilient.stream(MESSAGES)]
    assert chunks == ["a", "b"]
    assert breaker.state is CircuitState.CLOSED


async def test_stream_failure_records_failure_and_opens() -> None:
    clock = FakeClock()
    provider = FakeProvider(stream_sequences=[[RuntimeError("boom")]])
    breaker = CircuitBreaker(1, 10, now=clock)
    resilient = ResilientLLMProvider(
        provider=provider,
        rate_limiter=FixedWindowRateLimiter(10, 10, now=clock),
        circuit_breaker=breaker,
    )

    with pytest.raises(RuntimeError):
        async for _ in resilient.stream(MESSAGES):
            pass

    assert breaker.state is CircuitState.OPEN
