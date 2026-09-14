from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from typing import Any

from app.llm.contracts import JSONSchema, LLMMessage
from app.models.rag import RAG_EMBEDDING_DIMENSION
from app.rag.dense import hash_embed
from app.rag.retrieval import ChunkHit


class ContentLengthTokenCounter:
    """确定性测试计数器；生产组合根绝不会使用它。"""

    def count_messages(self, messages: Sequence[LLMMessage]) -> int:
        return sum(len(message.content) for message in messages)


def deterministic_embed(text: str) -> list[float]:
    """测试用确定性 512 维玩具 embedding。"""
    return hash_embed(text, dimensions=RAG_EMBEDDING_DIMENSION)


class DeterministicEmbedder:
    """测试用 Embedder，避免真库测试下载真实模型。"""

    @property
    def dimension(self) -> int:
        return RAG_EMBEDDING_DIMENSION

    def embed_query(self, text: str) -> list[float]:
        return deterministic_embed(text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [deterministic_embed(text) for text in texts]


class FakeRetriever:
    """显式测试替身，实现 AsyncRetriever 契约并记录调用参数。"""

    def __init__(self, hits: Sequence[ChunkHit]) -> None:
        self.hits = tuple(hits)
        self.calls: list[tuple[str, int, Mapping[str, Any] | None]] = []

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ChunkHit]:
        self.calls.append((query, top_k, metadata_filter))
        return self.hits

CompleteHandler = Callable[[Sequence[LLMMessage]], Awaitable[str]]
StreamHandler = Callable[[Sequence[LLMMessage]], AsyncIterator[str]]
StructuredOutputHandler = Callable[
    [Sequence[LLMMessage], JSONSchema], Awaitable[dict[str, Any]]
]


class FakeLLMProvider:
    """显式测试替身，实现应用 provider 契约。"""

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
