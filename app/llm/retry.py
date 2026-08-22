"""Provider-neutral retry policy for structured LLM output.

Retrying is not free: each attempt adds latency and token cost. The policy
therefore encodes *which* failures can recover by retrying and how long to
wait between attempts. It contains no HTTP or provider details.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.core.exceptions import (
    LLMConfigurationError,
    LLMResponseFormatError,
    LLMServiceError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.contracts import JSONSchema, LLMMessage, StructuredOutputProvider


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many attempts and how long to wait before retrying."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts, int
        ):
            raise TypeError("max_attempts must be an integer")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if isinstance(self.base_delay_seconds, bool) or not isinstance(
            self.base_delay_seconds, (int, float)
        ):
            raise TypeError("base_delay_seconds must be a number")
        if isinstance(self.max_delay_seconds, bool) or not isinstance(
            self.max_delay_seconds, (int, float)
        ):
            raise TypeError("max_delay_seconds must be a number")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")

    def delay_seconds(self, attempt: int) -> float:
        """Exponential backoff with a cap: base * 2^attempt."""
        return min(
            self.base_delay_seconds * (2**attempt),
            self.max_delay_seconds,
        )

    def should_retry(self, exc: LLMServiceError, attempt: int) -> bool:
        if attempt >= self.max_attempts - 1:
            return False
        if isinstance(exc, LLMConfigurationError):
            return False
        if isinstance(exc, LLMTimeoutError):
            return True
        if isinstance(exc, LLMUpstreamError):
            return exc.retryable
        if isinstance(exc, LLMResponseFormatError):
            return True
        return False


DEFAULT_RETRY_POLICY = RetryPolicy()


async def complete_structured_with_retry(
    provider: StructuredOutputProvider,
    messages: Sequence[LLMMessage],
    schema: JSONSchema,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> dict[str, Any]:
    """Call ``complete_structured`` and retry only policy-approved failures.

    The last error is re-raised after the final attempt; retries never silently
    swallow the failure.
    """
    last_error: LLMServiceError | None = None
    for attempt in range(policy.max_attempts):
        try:
            return await provider.complete_structured(messages, schema)
        except LLMServiceError as exc:
            last_error = exc
            if not policy.should_retry(exc, attempt):
                raise
            if attempt < policy.max_attempts - 1:
                await asyncio.sleep(policy.delay_seconds(attempt))

    assert last_error is not None
    raise last_error
