from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

from app.llm.contracts import JSONSchema, LLMMessage


class ContentLengthTokenCounter:
    """Deterministic test counter; never used by the production composition root."""

    def count_messages(self, messages: Sequence[LLMMessage]) -> int:
        return sum(len(message.content) for message in messages)

CompleteHandler = Callable[[Sequence[LLMMessage]], Awaitable[str]]
StreamHandler = Callable[[Sequence[LLMMessage]], AsyncIterator[str]]
StructuredOutputHandler = Callable[
    [Sequence[LLMMessage], JSONSchema], Awaitable[dict[str, Any]]
]


class FakeLLMProvider:
    """Explicit test double implementing the application provider contract."""

    def __init__(
        self,
        *,
        complete_result: str = "模拟完整回复",
        stream_chunks: Sequence[str] = ("模拟", "流式回复"),
        structured_result: dict[str, Any] | None = None,
    ) -> None:
        self.complete_result = complete_result
        self.stream_chunks = tuple(stream_chunks)
        self.structured_result = structured_result
        self.complete_handler: CompleteHandler | None = None
        self.stream_handler: StreamHandler | None = None
        self.structured_handler: StructuredOutputHandler | None = None
        self.complete_calls: list[tuple[LLMMessage, ...]] = []
        self.stream_calls: list[tuple[LLMMessage, ...]] = []
        self.structured_calls: list[
            tuple[tuple[LLMMessage, ...], JSONSchema]
        ] = []

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        self.complete_calls.append(tuple(messages))
        if self.complete_handler is not None:
            return await self.complete_handler(messages)
        return self.complete_result

    def stream(self, messages: Sequence[LLMMessage]) -> AsyncIterator[str]:
        self.stream_calls.append(tuple(messages))
        if self.stream_handler is not None:
            return self.stream_handler(messages)
        return self._default_stream()

    async def complete_structured(
        self,
        messages: Sequence[LLMMessage],
        schema: JSONSchema,
    ) -> dict[str, Any]:
        self.structured_calls.append((tuple(messages), schema))
        if self.structured_handler is not None:
            return await self.structured_handler(messages, schema)
        if self.structured_result is not None:
            return self.structured_result
        return {"topic": "AI", "sentiment": "positive"}

    async def _default_stream(self) -> AsyncIterator[str]:
        for chunk in self.stream_chunks:
            yield chunk
