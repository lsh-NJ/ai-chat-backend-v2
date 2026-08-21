from dataclasses import FrozenInstanceError

import pytest

from app.llm.contracts import LLMMessage, LLMProvider, LLMRole


class FakeProvider:
    async def complete(self, messages) -> str:
        return f"complete:{messages[-1].content}"

    async def stream(self, messages):
        yield "stream:"
        yield messages[-1].content


def test_llm_message_is_provider_neutral_and_immutable() -> None:
    message = LLMMessage(role=LLMRole.USER, content="你好")

    assert message.role == LLMRole.USER
    assert message.content == "你好"
    with pytest.raises(FrozenInstanceError):
        message.content = "已修改"  # type: ignore[misc]


def test_llm_message_rejects_role_outside_contract() -> None:
    with pytest.raises(TypeError, match="role must be an LLMRole"):
        LLMMessage(role="tool", content="result")  # type: ignore[arg-type]


async def test_structural_provider_supports_complete_and_stream() -> None:
    provider: LLMProvider = FakeProvider()
    messages = [LLMMessage(role=LLMRole.USER, content="你好")]

    assert isinstance(provider, LLMProvider)
    assert await provider.complete(messages) == "complete:你好"
    assert [chunk async for chunk in provider.stream(messages)] == [
        "stream:",
        "你好",
    ]
