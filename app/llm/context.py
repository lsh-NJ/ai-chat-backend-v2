"""Provider-neutral selection of a contiguous, recent chat history."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.exceptions import LLMInputTooLongError
from app.llm.contracts import LLMMessage, LLMRole
from app.llm.tokenization import ContextBudget, ContextUsage, TokenCounter


@dataclass(frozen=True, slots=True)
class ContextSelection:
    messages: tuple[LLMMessage, ...]
    usage: ContextUsage
    selected_turns: int
    dropped_turns: int


class ContextSelector:
    """Keep required messages plus the largest contiguous recent turn suffix."""

    def __init__(self, counter: TokenCounter, budget: ContextBudget) -> None:
        self._counter = counter
        self._budget = budget

    def select(
        self,
        *,
        system: LLMMessage,
        history: Sequence[LLMMessage],
        current: LLMMessage,
    ) -> ContextSelection:
        if system.role is not LLMRole.SYSTEM:
            raise ValueError("system message must use the system role")
        if current.role is not LLMRole.USER:
            raise ValueError("current message must use the user role")

        required = (system, current)
        required_usage = self._budget.measure(self._counter, required)
        if not required_usage.fits:
            raise LLMInputTooLongError(
                "系统提示词与当前输入超过模型输入预算"
            )

        turns = self._group_complete_turns(history)
        selected: list[tuple[LLMMessage, ...]] = []
        usage = required_usage

        for turn in reversed(turns):
            candidate_turns = [turn, *selected]
            candidate = (
                system,
                *(message for item in candidate_turns for message in item),
                current,
            )
            candidate_usage = self._budget.measure(self._counter, candidate)
            if not candidate_usage.fits:
                break
            selected = candidate_turns
            usage = candidate_usage

        messages = (
            system,
            *(message for turn in selected for message in turn),
            current,
        )
        return ContextSelection(
            messages=messages,
            usage=usage,
            selected_turns=len(selected),
            dropped_turns=len(turns) - len(selected),
        )

    @staticmethod
    def _group_complete_turns(
        history: Sequence[LLMMessage],
    ) -> tuple[tuple[LLMMessage, ...], ...]:
        turns: list[tuple[LLMMessage, ...]] = []
        pending_user: LLMMessage | None = None

        for message in history:
            if message.role is LLMRole.SYSTEM:
                raise ValueError("stored history must not contain system messages")

            if message.role is LLMRole.USER:
                pending_user = message
                continue

            if pending_user is None:
                raise ValueError("assistant history message has no preceding user")

            turns.append((pending_user, message))
            pending_user = None

        # A user message without an assistant response is not a complete past turn.
        return tuple(turns)
