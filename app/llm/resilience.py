"""本地限流与熔断的 provider-neutral 实现。

Rate Limiter 保护上游：在调用前控制速率，避免突发流量触发 429。
Circuit Breaker 保护调用方和上游：provider 连续失败时快速失败，
避免把雪崩放大；经过恢复时间后进入 half-open 允许小流量试探。
"""

import time
from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from typing import Callable

from app.llm.contracts import LLMMessage, LLMProvider


class RateLimitExceededError(Exception):
    """本地限流拒绝本次调用。"""


class CircuitOpenError(Exception):
    """熔断器处于 open/half-open 拒绝本次调用。"""


class FixedWindowRateLimiter:
    """固定窗口限流器。

    ``max_requests`` 表示每个 ``window_seconds`` 内最多放行多少次。
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(max_requests, bool) or not isinstance(max_requests, int):
            raise TypeError("max_requests must be an integer")
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if isinstance(window_seconds, bool) or not isinstance(
            window_seconds, (int, float)
        ):
            raise TypeError("window_seconds must be a number")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_requests = max_requests
        self._window_seconds = float(window_seconds)
        self._now = now or time.monotonic
        self._window_start = 0.0
        self._count = 0

    def allow(self) -> bool:
        """返回 True 表示本次请求被放行，False 表示超限。"""

        now = self._now()
        if now - self._window_start >= self._window_seconds:
            self._window_start = now
            self._count = 0
        if self._count >= self._max_requests:
            return False
        self._count += 1
        return True


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """连续失败后打开，经过恢复时间后 half-open 试探。"""

    def __init__(
        self,
        failure_threshold: int,
        recovery_timeout_seconds: float,
        *,
        half_open_max_calls: int = 1,
        now: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(failure_threshold, bool) or not isinstance(
            failure_threshold, int
        ):
            raise TypeError("failure_threshold must be an integer")
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if isinstance(recovery_timeout_seconds, bool) or not isinstance(
            recovery_timeout_seconds, (int, float)
        ):
            raise TypeError("recovery_timeout_seconds must be a number")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be positive")
        if isinstance(half_open_max_calls, bool) or not isinstance(
            half_open_max_calls, int
        ):
            raise TypeError("half_open_max_calls must be an integer")
        if half_open_max_calls <= 0:
            raise ValueError("half_open_max_calls must be positive")

        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = float(recovery_timeout_seconds)
        self._half_open_max_calls = half_open_max_calls
        self._now = now or time.monotonic

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        return self._state

    def before_call(self) -> None:
        """调用前检查；熔断打开或 half-open 预算用完时拒绝。"""

        now = self._now()
        if self._state is CircuitState.OPEN:
            if now - self._opened_at >= self._recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
            else:
                raise CircuitOpenError("circuit is open")

        if self._state is CircuitState.HALF_OPEN:
            if self._half_open_calls >= self._half_open_max_calls:
                raise CircuitOpenError("circuit half-open trial budget is exhausted")
            self._half_open_calls += 1

    def record_success(self) -> None:
        """成功调用：关闭熔断并清零连续失败。"""

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_calls = 0

    def record_failure(self) -> None:
        """失败调用：累计连续失败；达到阈值或 half-open 失败时打开。"""

        self._consecutive_failures += 1
        if (
            self._state is CircuitState.HALF_OPEN
            or self._consecutive_failures >= self._failure_threshold
        ):
            self._state = CircuitState.OPEN
            self._opened_at = self._now()
            self._half_open_calls = 0


class ResilientLLMProvider:
    """给单个 provider 套上限流和熔断的普通 LLMProvider。"""

    def __init__(
        self,
        provider: LLMProvider,
        rate_limiter: FixedWindowRateLimiter,
        circuit_breaker: CircuitBreaker,
    ) -> None:
        if not isinstance(provider, LLMProvider):
            raise TypeError("provider must satisfy LLMProvider")
        self._provider = provider
        self._rate_limiter = rate_limiter
        self._circuit_breaker = circuit_breaker

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        if not self._rate_limiter.allow():
            raise RateLimitExceededError("rate limit exceeded")
        self._circuit_breaker.before_call()
        try:
            result = await self._provider.complete(messages)
        except Exception:
            self._circuit_breaker.record_failure()
            raise
        self._circuit_breaker.record_success()
        return result

    async def stream(
        self,
        messages: Sequence[LLMMessage],
    ) -> AsyncIterator[str]:
        if not self._rate_limiter.allow():
            raise RateLimitExceededError("rate limit exceeded")
        self._circuit_breaker.before_call()
        try:
            async for chunk in self._provider.stream(messages):
                yield chunk
        except Exception:
            self._circuit_breaker.record_failure()
            raise
        self._circuit_breaker.record_success()
