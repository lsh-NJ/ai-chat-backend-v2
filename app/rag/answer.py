"""RAG 问答层领域模型（Week 16 Day 1）。

这里定义的是“检索之后、生成前后”的服务契约，不依赖 FastAPI、数据库会话
或任何具体 LLM 供应商。上层 API、评测 harness 和后续引用校验都复用这些类型。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.llm.contracts import LLMMessage
from app.llm.tokenization import ContextUsage


def _require_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _validate_score(score: object) -> float:
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError("score must be a number")
    value = float(score)
    if not math.isfinite(value):
        raise ValueError("score must be finite")
    return value


@dataclass(frozen=True, slots=True)
class RagCitation:
    """回答引用的一条证据，指向一个可追踪回原文的 chunk。

    `label` 由系统生成，例如 "1" 对应 prompt 中的 `[1]`；
    它不是模型自由输出的文本。
    """

    label: str
    chunk_id: str
    document_id: str
    source: str
    start: int
    end: int
    score: float

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.isdigit():
            raise ValueError("citation label must be a numeric string")
        if int(self.label) <= 0:
            raise ValueError("citation label must be positive")
        _require_non_empty_string(self.chunk_id, "citation chunk_id")
        _require_non_empty_string(self.document_id, "citation document_id")
        _require_non_empty_string(self.source, "citation source")
        if (
            isinstance(self.start, bool)
            or not isinstance(self.start, int)
            or isinstance(self.end, bool)
            or not isinstance(self.end, int)
        ):
            raise TypeError("citation start/end must be integers")
        if self.start < 0 or self.end <= self.start:
            raise ValueError(
                "citation start/end must satisfy 0 <= start < end"
            )
        object.__setattr__(self, "score", _validate_score(self.score))


@dataclass(frozen=True, slots=True)
class RagAnswer:
    """RAG 服务对一次问题的完整回答。"""

    answer: str
    citations: tuple[RagCitation, ...]
    retrieved_chunk_ids: tuple[str, ...]
    refused: bool = False

    def __post_init__(self) -> None:
        _require_non_empty_string(self.answer, "answer")
        if not isinstance(self.citations, tuple):
            raise TypeError("citations must be a tuple")
        if any(not isinstance(citation, RagCitation) for citation in self.citations):
            raise TypeError("citations must contain RagCitation values")
        labels = [citation.label for citation in self.citations]
        if len(labels) != len(set(labels)):
            raise ValueError("citation labels must be unique")
        if not isinstance(self.retrieved_chunk_ids, tuple):
            raise TypeError("retrieved_chunk_ids must be a tuple")
        if any(
            not isinstance(chunk_id, str) or not chunk_id.strip()
            for chunk_id in self.retrieved_chunk_ids
        ):
            raise ValueError("retrieved chunk ids must be non-empty strings")
        if not isinstance(self.refused, bool):
            raise TypeError("refused must be a boolean")


@dataclass(frozen=True, slots=True)
class RagContext:
    """即将发给 LLM 的上下文及其引用映射。"""

    messages: tuple[LLMMessage, ...]
    citations: tuple[RagCitation, ...]
    retrieved_chunk_ids: tuple[str, ...]
    usage: ContextUsage

    def __post_init__(self) -> None:
        if not isinstance(self.messages, tuple):
            raise TypeError("messages must be a tuple")
        if any(not isinstance(message, LLMMessage) for message in self.messages):
            raise TypeError("messages must contain LLMMessage values")
        if not isinstance(self.citations, tuple):
            raise TypeError("citations must be a tuple")
        if any(not isinstance(citation, RagCitation) for citation in self.citations):
            raise TypeError("citations must contain RagCitation values")
        if not isinstance(self.retrieved_chunk_ids, tuple):
            raise TypeError("retrieved_chunk_ids must be a tuple")
        if not isinstance(self.usage, ContextUsage):
            raise TypeError("usage must be a ContextUsage")
