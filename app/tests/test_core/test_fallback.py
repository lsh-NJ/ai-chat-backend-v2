from collections.abc import AsyncIterator, Sequence

import pytest

from app.core.exceptions import (
    LLMConfigurationError,
    LLMStreamError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.contracts import LLMMessage, LLMRole
from app.llm.fallback import FallbackLLMProvider
from app.llm.retry import RetryPolicy
from app.llm.routing import ProviderCandidate, ReliabilityPolicy

NO_RETRY = RetryPolicy(
    max_attempts=1,
    base_delay_seconds=0,
    max_delay_seconds=0,
)
FAST_RETRY = RetryPolicy(
    max_attempts=3,
    base_delay_seconds=0,
    max_delay_seconds=0,
)
MESSAGES = [LLMMessage(role=LLMRole.USER, content="你好")]


class ScriptedProvider:
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


def _provider(
    primary: ScriptedProvider,
    fallback: ScriptedProvider,
    *,
    max_fallback_attempts: int = 1,
    retry_policy: RetryPolicy = NO_RETRY,
) -> FallbackLLMProvider:
    return FallbackLLMProvider(
        candidates=[
            ProviderCandidate("primary", primary),
            ProviderCandidate("fallback", fallback),
        ],
        policy=ReliabilityPolicy(
            retry_policy=retry_policy,
            max_fallback_attempts=max_fallback_attempts,
        ),
    )


async def test_complete_falls_back_after_primary_timeout() -> None:
    primary = ScriptedProvider(complete_results=[LLMTimeoutError("timeout")])
    fallback = ScriptedProvider(complete_results=["fallback"])

    provider = _provider(primary, fallback)
    result = await provider.complete(MESSAGES)

    assert result == "fallback"
    assert primary.complete_calls == 1
    assert fallback.complete_calls == 1


async def test_complete_retries_primary_before_fallback() -> None:
    primary = ScriptedProvider(
        complete_results=[
            LLMTimeoutError("timeout-1"),
            LLMTimeoutError("timeout-2"),
            "primary",
        ]
    )
    fallback = ScriptedProvider(complete_results=["fallback"])

    provider = _provider(primary, fallback, retry_policy=FAST_RETRY)
    result = await provider.complete(MESSAGES)

    assert result == "primary"
    assert primary.complete_calls == 3
    assert fallback.complete_calls == 0


async def test_complete_falls_back_after_primary_retries_exhausted() -> None:
    primary = ScriptedProvider(
        complete_results=[
            LLMTimeoutError("timeout-1"),
            LLMTimeoutError("timeout-2"),
            LLMTimeoutError("timeout-3"),
        ]
    )
    fallback = ScriptedProvider(complete_results=["fallback"])

    provider = _provider(primary, fallback, retry_policy=FAST_RETRY)
    result = await provider.complete(MESSAGES)

    assert result == "fallback"
    assert primary.complete_calls == 3
    assert fallback.complete_calls == 1


async def test_complete_does_not_fallback_on_configuration_error() -> None:
    primary = ScriptedProvider(
        complete_results=[LLMConfigurationError("bad config")]
    )
    fallback = ScriptedProvider(complete_results=["fallback"])

    provider = _provider(primary, fallback)
    with pytest.raises(LLMConfigurationError):
        await provider.complete(MESSAGES)

    assert fallback.complete_calls == 0


async def test_complete_falls_back_on_429_rate_limit() -> None:
    primary = ScriptedProvider(
        complete_results=[LLMUpstreamError("rate limited", status_code=429)]
    )
    fallback = ScriptedProvider(complete_results=["fallback"])

    provider = _provider(primary, fallback)
    result = await provider.complete(MESSAGES)

    assert result == "fallback"
    assert fallback.complete_calls == 1


async def test_complete_does_not_fallback_on_400_bad_request() -> None:
    primary = ScriptedProvider(
        complete_results=[LLMUpstreamError("bad request", status_code=400)]
    )
    fallback = ScriptedProvider(complete_results=["fallback"])

    provider = _provider(primary, fallback)
    with pytest.raises(LLMUpstreamError):
        await provider.complete(MESSAGES)

    assert fallback.complete_calls == 0


async def test_complete_respects_zero_fallback_attempts() -> None:
    primary = ScriptedProvider(complete_results=[LLMTimeoutError("timeout")])
    fallback = ScriptedProvider(complete_results=["fallback"])

    provider = _provider(primary, fallback, max_fallback_attempts=0)
    with pytest.raises(LLMTimeoutError):
        await provider.complete(MESSAGES)

    assert fallback.complete_calls == 0


async def test_complete_raises_last_error_when_all_candidates_fail() -> None:
    primary = ScriptedProvider(
        complete_results=[
            LLMTimeoutError("primary-1"),
            LLMTimeoutError("primary-2"),
            LLMTimeoutError("primary-3"),
        ]
    )
    fallback = ScriptedProvider(
        complete_results=[
            LLMTimeoutError("fallback-1"),
            LLMTimeoutError("fallback-2"),
            LLMTimeoutError("fallback-3"),
        ]
    )

    provider = _provider(primary, fallback, retry_policy=FAST_RETRY)
    with pytest.raises(LLMTimeoutError, match="fallback-3"):
        await provider.complete(MESSAGES)


async def test_stream_falls_back_before_first_chunk() -> None:
    primary = ScriptedProvider(stream_sequences=[[LLMTimeoutError("timeout")]])
    fallback = ScriptedProvider(stream_sequences=[["fallback"]])

    provider = _provider(primary, fallback)
    chunks = [chunk async for chunk in provider.stream(MESSAGES)]

    assert chunks == ["fallback"]
    assert primary.stream_calls == 1
    assert fallback.stream_calls == 1


async def test_stream_does_not_fallback_after_partial_output() -> None:
    primary = ScriptedProvider(
        stream_sequences=[["partial", LLMStreamError("broken")]]
    )
    fallback = ScriptedProvider(stream_sequences=[["fallback"]])

    provider = _provider(primary, fallback)
    collected: list[str] = []
    with pytest.raises(LLMStreamError):
        async for chunk in provider.stream(MESSAGES):
            collected.append(chunk)

    assert collected == ["partial"]
    assert fallback.stream_calls == 0


async def test_stream_retries_before_first_chunk() -> None:
    primary = ScriptedProvider(
        stream_sequences=[
            [LLMTimeoutError("timeout-1")],
            ["primary"],
        ]
    )
    fallback = ScriptedProvider(stream_sequences=[["fallback"]])

    provider = _provider(primary, fallback, retry_policy=FAST_RETRY)
    chunks = [chunk async for chunk in provider.stream(MESSAGES)]

    assert chunks == ["primary"]
    assert primary.stream_calls == 2
    assert fallback.stream_calls == 0


def test_fallback_provider_requires_at_least_one_candidate() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FallbackLLMProvider(
            candidates=[],
            policy=ReliabilityPolicy(),
        )
