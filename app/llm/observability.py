"""Provider-neutral 的 LLM 调用可观测性与成本估算。

本模块在 provider 边界统一记录 model、prompt version、latency、
tokens、cost、error 和 trace id；不记录用户消息正文或模型输出正文。
"""

import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.llm.contracts import LLMMessage, LLMProvider


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """一次调用的 token 用量。"""

    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must not be negative")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class LLMCallRecord:
    """一次 LLM 调用的结构化观测记录。"""

    trace_id: str
    model: str
    prompt_version: str
    latency_seconds: float
    success: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    error_type: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.trace_id, str) or not self.trace_id.strip():
            raise ValueError("trace_id must be a non-empty string")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(self.prompt_version, str) or not self.prompt_version.strip():
            raise ValueError("prompt_version must be a non-empty string")
        if isinstance(self.latency_seconds, bool) or not isinstance(
            self.latency_seconds, (int, float)
        ):
            raise TypeError("latency_seconds must be a number")
        if self.latency_seconds < 0:
            raise ValueError("latency_seconds must not be negative")


class CallRecorder(Protocol):
    """接收一条调用记录。实现方决定写日志、内存列表还是指标后端。"""

    def record(self, record: LLMCallRecord) -> None:
        ...


class CostCalculator(Protocol):
    """根据 token 用量和模型名估算成本。"""

    def calculate(self, usage: TokenUsage, model: str) -> float:
        ...


TokenCounter = Callable[[Sequence[LLMMessage], str], TokenUsage]


class RecordingLLMProvider:
    """在普通 LLMProvider 外层记录调用观测数据。"""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str,
        prompt_version: str,
        token_counter: TokenCounter,
        cost_calculator: CostCalculator,
        recorder: CallRecorder,
        trace_id_provider: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(provider, LLMProvider):
            raise TypeError("provider must satisfy LLMProvider")
        self._provider = provider
        self._model = model
        self._prompt_version = prompt_version
        self._token_counter = token_counter
        self._cost_calculator = cost_calculator
        self._recorder = recorder
        self._trace_id_provider = trace_id_provider or (
            lambda: uuid.uuid4().hex
        )

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        trace_id = self._trace_id_provider()
        started_at = time.monotonic()
        try:
            result = await self._provider.complete(messages)
        except Exception as exc:
            self._record(
                trace_id=trace_id,
                started_at=started_at,
                success=False,
                error=exc,
            )
            raise

        self._record(
            trace_id=trace_id,
            started_at=started_at,
            success=True,
            usage=self._token_counter(messages, result),
            error=None,
        )
        return result

    async def stream(
        self,
        messages: Sequence[LLMMessage],
    ) -> AsyncIterator[str]:
        trace_id = self._trace_id_provider()
        started_at = time.monotonic()
        chunks: list[str] = []
        try:
            async for chunk in self._provider.stream(messages):
                chunks.append(chunk)
                yield chunk
        except Exception as exc:
            self._record(
                trace_id=trace_id,
                started_at=started_at,
                success=False,
                error=exc,
            )
            raise

        output = "".join(chunks)
        self._record(
            trace_id=trace_id,
            started_at=started_at,
            success=True,
            usage=self._token_counter(messages, output),
            error=None,
        )

    def _record(
        self,
        *,
        trace_id: str,
        started_at: float,
        success: bool,
        error: Exception | None,
        usage: TokenUsage | None = None,
    ) -> None:
        latency_seconds = time.monotonic() - started_at
        cost = (
            self._cost_calculator.calculate(usage, self._model)
            if success and usage is not None
            else None
        )
        self._recorder.record(
            LLMCallRecord(
                trace_id=trace_id,
                model=self._model,
                prompt_version=self._prompt_version,
                latency_seconds=latency_seconds,
                success=success,
                input_tokens=usage.input_tokens if usage is not None else None,
                output_tokens=usage.output_tokens if usage is not None else None,
                cost=cost,
                error_type=type(error).__name__ if error is not None else None,
                error_message=str(error) if error is not None else None,
            )
        )
