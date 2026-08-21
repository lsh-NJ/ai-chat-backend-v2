"""Application-facing contracts for LLM capabilities.

This module deliberately contains no HTTP, environment, or provider-specific
details. Application services depend on these types; concrete adapters depend
on this contract and translate it to an upstream protocol.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class LLMRole(StrEnum):
    """Conversation roles understood by the application."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """An immutable, provider-neutral message."""

    role: LLMRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, LLMRole):
            raise TypeError("role must be an LLMRole")
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")


@runtime_checkable
class LLMProvider(Protocol):
    """The model capabilities required by the Chat application service."""

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Return one complete assistant response."""
        ...

    def stream(
        self,
        messages: Sequence[LLMMessage],
    ) -> AsyncIterator[str]:
        """Return an iterator of text chunks; iteration raises on failure."""
        ...
