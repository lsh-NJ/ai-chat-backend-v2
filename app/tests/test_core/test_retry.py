import pytest

from app.core.exceptions import (
    LLMConfigurationError,
    LLMResponseFormatError,
    LLMServiceError,
    LLMStreamError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.contracts import LLMMessage, LLMRole
from app.llm.retry import RetryPolicy, complete_structured_with_retry

MESSAGES = [LLMMessage(role=LLMRole.USER, content="测试")]
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


class FlakyStructuredProvider:
    def __init__(
        self,
        *,
        failure_count: int,
        error: LLMServiceError,
        result: dict | None = None,
    ) -> None:
        self.failure_count = failure_count
        self.error = error
        self.result = result if result is not None else {"ok": True}
        self.calls = 0

    async def complete_structured(self, messages, schema):
        self.calls += 1
        if self.calls <= self.failure_count:
            raise self.error
        return self.result


def _no_wait_policy() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=3,
        base_delay_seconds=0,
        max_delay_seconds=0,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"max_attempts": -1},
        {"base_delay_seconds": -0.1},
        {"max_delay_seconds": 0.05, "base_delay_seconds": 0.1},
    ],
)
def test_retry_policy_rejects_invalid_configuration(kwargs: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        RetryPolicy(**kwargs)


async def test_retry_succeeds_after_transient_timeouts() -> None:
    provider = FlakyStructuredProvider(
        failure_count=2,
        error=LLMTimeoutError("timeout"),
    )

    result = await complete_structured_with_retry(
        provider,
        MESSAGES,
        SCHEMA,
        _no_wait_policy(),
    )

    assert result == {"ok": True}
    assert provider.calls == 3


async def test_retry_succeeds_after_retryable_upstream_5xx() -> None:
    provider = FlakyStructuredProvider(
        failure_count=2,
        error=LLMUpstreamError("upstream 503", status_code=503),
    )

    result = await complete_structured_with_retry(
        provider,
        MESSAGES,
        SCHEMA,
        _no_wait_policy(),
    )

    assert result == {"ok": True}
    assert provider.calls == 3


async def test_retry_raises_last_error_after_max_attempts() -> None:
    provider = FlakyStructuredProvider(
        failure_count=99,
        error=LLMResponseFormatError("bad json"),
    )

    with pytest.raises(LLMResponseFormatError, match="bad json"):
        await complete_structured_with_retry(
            provider,
            MESSAGES,
            SCHEMA,
            _no_wait_policy(),
        )

    assert provider.calls == 3


async def test_retry_does_not_retry_configuration_error() -> None:
    provider = FlakyStructuredProvider(
        failure_count=99,
        error=LLMConfigurationError("bad config"),
    )

    with pytest.raises(LLMConfigurationError):
        await complete_structured_with_retry(
            provider,
            MESSAGES,
            SCHEMA,
            _no_wait_policy(),
        )

    assert provider.calls == 1


async def test_retry_does_not_retry_4xx_upstream_error() -> None:
    provider = FlakyStructuredProvider(
        failure_count=99,
        error=LLMUpstreamError("bad request", status_code=400),
    )

    with pytest.raises(LLMUpstreamError):
        await complete_structured_with_retry(
            provider,
            MESSAGES,
            SCHEMA,
            _no_wait_policy(),
        )

    assert provider.calls == 1


async def test_retry_does_not_retry_unclassified_llm_error() -> None:
    provider = FlakyStructuredProvider(
        failure_count=99,
        error=LLMStreamError("stream error"),
    )

    with pytest.raises(LLMStreamError):
        await complete_structured_with_retry(
            provider,
            MESSAGES,
            SCHEMA,
            _no_wait_policy(),
        )

    assert provider.calls == 1


def test_delay_uses_exponential_backoff_with_cap() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=0.1,
        max_delay_seconds=0.25,
    )

    assert policy.delay_seconds(0) == 0.1
    assert policy.delay_seconds(1) == 0.2
    assert policy.delay_seconds(2) == 0.25
    assert policy.delay_seconds(3) == 0.25
