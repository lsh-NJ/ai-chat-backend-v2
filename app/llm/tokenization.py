"""Provider-neutral token counting and context budget primitives."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.llm.contracts import LLMMessage


class TokenCounter(Protocol):
    """Count model input units for a complete serialized message sequence."""

    def count_messages(self, messages: Sequence[LLMMessage]) -> int: ...


@dataclass(frozen=True, slots=True)
class ContextUsage:
    used_tokens: int
    max_input_tokens: int

    @property
    def remaining_tokens(self) -> int:
        return self.max_input_tokens - self.used_tokens

    @property
    def fits(self) -> bool:
        return self.used_tokens <= self.max_input_tokens


@dataclass(frozen=True, slots=True)
class ContextBudget:
    context_window: int
    output_reserve: int
    safety_margin: int = 0

    def __post_init__(self) -> None:
        values = {
            "context_window": self.context_window,
            "output_reserve": self.output_reserve,
            "safety_margin": self.safety_margin,
        }
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")

        if self.context_window <= 0:
            raise ValueError("context_window must be positive")
        if self.output_reserve < 0:
            raise ValueError("output_reserve must not be negative")
        if self.safety_margin < 0:
            raise ValueError("safety_margin must not be negative")
        if self.output_reserve + self.safety_margin >= self.context_window:
            raise ValueError(
                "output_reserve plus safety_margin must be smaller than "
                "context_window"
            )

    @property
    def max_input_tokens(self) -> int:
        return self.context_window - self.output_reserve - self.safety_margin

    def measure(
        self,
        counter: TokenCounter,
        messages: Sequence[LLMMessage],
    ) -> ContextUsage:
        used_tokens = counter.count_messages(messages)
        if isinstance(used_tokens, bool) or not isinstance(used_tokens, int):
            raise TypeError("token counter must return an integer")
        if used_tokens < 0:
            raise ValueError("token counter must not return a negative value")
        return ContextUsage(
            used_tokens=used_tokens,
            max_input_tokens=self.max_input_tokens,
        )
