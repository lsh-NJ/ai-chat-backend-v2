"""Provider-neutral 的 fallback 组合实现。

Fallback 与 retry 的职责不同：
- retry：同一 provider 对同一次请求再试；
- fallback：同一 provider 重试仍失败后，换一个 provider。

本模块同时保证流式 fallback 只发生在“尚未产出任何 chunk”之前；
一旦已经有部分内容发给客户端，就不能再切换到其他 provider。
"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from app.core.exceptions import (
    LLMConfigurationError,
    LLMInputTooLongError,
    LLMResponseFormatError,
    LLMServiceError,
    LLMStreamError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.contracts import LLMMessage, LLMProvider
from app.llm.retry import RetryPolicy
from app.llm.routing import (
    FirstCandidateStrategy,
    ProviderCandidate,
    ReliabilityPolicy,
    RoutingStrategy,
)


def should_fallback(exc: LLMServiceError) -> bool:
    """判断某个错误是否值得换 provider。

    配置错误和输入超长属于应用/请求自身问题，fallback 无法修复；
    超时、5xx/网络错误和 429 限流属于 provider 侧问题，值得 fallback；
    响应格式错误也可能因 provider 而异，允许 fallback。
    """

    if isinstance(exc, LLMConfigurationError):
        return False
    if isinstance(exc, LLMInputTooLongError):
        return False
    if isinstance(exc, LLMTimeoutError):
        return True
    if isinstance(exc, LLMUpstreamError):
        return exc.retryable or exc.status_code == 429
    if isinstance(exc, LLMResponseFormatError):
        return True
    if isinstance(exc, LLMStreamError):
        return True
    return False


class FallbackLLMProvider:
    """在多个候选 provider 之间执行“重试 → fallback”的 LLMProvider。

    它实现 ``LLMProvider``，因此业务层可以把它当作普通 provider 使用，
    不需要知道内部谁是主、谁是备。
    """

    def __init__(
        self,
        candidates: Sequence[ProviderCandidate],
        policy: ReliabilityPolicy,
        strategy: RoutingStrategy | None = None,
    ) -> None:
        if not candidates:
            raise ValueError("at least one provider candidate is required")
        self._candidates = tuple(candidates)
        self._policy = policy
        self._strategy = strategy or FirstCandidateStrategy()

    def _ordered_candidates(
        self,
        messages: Sequence[LLMMessage],
    ) -> tuple[ProviderCandidate, ...]:
        """按“策略选出的主候选优先，其余保持原顺序”构造尝试顺序。"""

        primary = self._strategy.select(self._candidates, messages)
        return (primary,) + tuple(
            candidate for candidate in self._candidates if candidate is not primary
        )

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        last_error: LLMServiceError | None = None
        fallback_used = 0
        for candidate in self._ordered_candidates(messages):
            if fallback_used > self._policy.max_fallback_attempts:
                break
            try:
                return await self._complete_with_retry(
                    candidate.provider,
                    messages,
                    self._policy.retry_policy,
                )
            except LLMServiceError as exc:
                last_error = exc
                if not should_fallback(exc):
                    raise
                fallback_used += 1

        assert last_error is not None
        raise last_error

    async def stream(
        self,
        messages: Sequence[LLMMessage],
    ) -> AsyncIterator[str]:
        last_error: LLMServiceError | None = None
        fallback_used = 0
        for candidate in self._ordered_candidates(messages):
            if fallback_used > self._policy.max_fallback_attempts:
                break
            started = False
            try:
                async for chunk in self._stream_with_retry_before_first(
                    candidate.provider,
                    messages,
                    self._policy.retry_policy,
                ):
                    started = True
                    yield chunk
                return
            except LLMServiceError as exc:
                if started or not should_fallback(exc):
                    raise
                last_error = exc
                fallback_used += 1

        assert last_error is not None
        raise last_error

    @staticmethod
    async def _complete_with_retry(
        provider: LLMProvider,
        messages: Sequence[LLMMessage],
        policy: RetryPolicy,
    ) -> str:
        last_error: LLMServiceError | None = None
        for attempt in range(policy.max_attempts):
            try:
                return await provider.complete(messages)
            except LLMServiceError as exc:
                last_error = exc
                if not policy.should_retry(exc, attempt):
                    raise
                if attempt < policy.max_attempts - 1:
                    await asyncio.sleep(policy.delay_seconds(attempt))

        assert last_error is not None
        raise last_error

    @staticmethod
    async def _stream_with_retry_before_first(
        provider: LLMProvider,
        messages: Sequence[LLMMessage],
        policy: RetryPolicy,
    ) -> AsyncIterator[str]:
        """只在“第一个 chunk 之前”重试；一旦产出内容就不再重试。"""

        attempt = 0
        while True:
            iterator = provider.stream(messages)
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                return
            except LLMServiceError as exc:
                if attempt >= policy.max_attempts - 1 or not policy.should_retry(
                    exc, attempt
                ):
                    raise
                await asyncio.sleep(policy.delay_seconds(attempt))
                attempt += 1
                continue

            yield first
            async for chunk in iterator:
                yield chunk
            return
