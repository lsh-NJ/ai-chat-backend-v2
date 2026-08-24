from dataclasses import FrozenInstanceError

import pytest

from app.llm.contracts import (
    LLMMessage,
    LLMProvider,
    LLMRole,
    StructuredOutputProvider,
    TextCompletion,
    ToolCall,
    ToolCallingProvider,
    ToolCallRequest,
    ToolDefinition,
    ToolResultMessage,
)


class FakeProvider:
    async def complete(self, messages) -> str:
        return f"complete:{messages[-1].content}"

    async def stream(self, messages):
        yield "stream:"
        yield messages[-1].content


class FakeStructuredProvider:
    async def complete_structured(self, messages, schema):
        return {"ok": True}


class FakeToolCallingProvider:
    async def complete_with_tools(self, messages, tools):
        return TextCompletion(content=f"tools:{len(tools)}")


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


async def test_structural_structured_output_provider_contract() -> None:
    provider: StructuredOutputProvider = FakeStructuredProvider()
    messages = [LLMMessage(role=LLMRole.USER, content="你好")]
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    assert isinstance(provider, StructuredOutputProvider)
    assert await provider.complete_structured(messages, schema) == {"ok": True}


async def test_structural_tool_calling_provider_is_an_independent_capability() -> None:
    provider: ToolCallingProvider = FakeToolCallingProvider()
    messages = [LLMMessage(role=LLMRole.USER, content="查询会话")]
    tools = [
        ToolDefinition(
            name="get_conversation",
            description="读取当前用户拥有的会话",
            parameters={"type": "object"},
        )
    ]

    assert isinstance(provider, ToolCallingProvider)
    assert not isinstance(FakeProvider(), ToolCallingProvider)
    assert await provider.complete_with_tools(messages, tools) == TextCompletion(
        content="tools:1"
    )


def test_tool_call_request_preserves_multiple_calls_and_correlation_ids() -> None:
    first = ToolCall(
        call_id="call-1",
        name="get_conversation",
        arguments={"conversation_id": 1},
    )
    second = ToolCall(
        call_id="call-2",
        name="get_conversation",
        arguments={"conversation_id": 2},
    )

    request = ToolCallRequest(tool_calls=(first, second), content="正在查询")
    results = (
        ToolResultMessage(call_id="call-1", content='{"title":"第一条"}'),
        ToolResultMessage(call_id="call-2", content='{"title":"第二条"}'),
    )

    assert request.tool_calls == (first, second)
    assert [result.call_id for result in results] == ["call-1", "call-2"]


def test_tool_call_arguments_are_copied_into_a_read_only_mapping() -> None:
    source = {"conversation_id": 1}
    call = ToolCall(
        call_id="call-1",
        name="get_conversation",
        arguments=source,
    )

    source["conversation_id"] = 2
    assert call.arguments["conversation_id"] == 1
    with pytest.raises(TypeError):
        call.arguments["conversation_id"] = 3  # type: ignore[index]


def test_tool_definition_parameters_are_copied_into_a_read_only_mapping() -> None:
    source = {"type": "object"}
    definition = ToolDefinition("lookup", "查询", source)

    source["type"] = "array"
    assert definition.parameters["type"] == "object"
    with pytest.raises(TypeError):
        definition.parameters["type"] = "string"  # type: ignore[index]


@pytest.mark.parametrize(
    ("factory", "error_type", "message"),
    [
        (
            lambda: ToolDefinition("", "读取会话", {"type": "object"}),
            ValueError,
            "tool name",
        ),
        (
            lambda: ToolCall("", "get_conversation", {}),
            ValueError,
            "call_id",
        ),
        (
            lambda: ToolCall("call-1", "", {}),
            ValueError,
            "call name",
        ),
        (
            lambda: ToolCall("call-1", "tool", []),  # type: ignore[arg-type]
            TypeError,
            "JSON object",
        ),
        (
            lambda: ToolCallRequest(tool_calls=()),
            ValueError,
            "must not be empty",
        ),
        (
            lambda: ToolCallRequest(
                tool_calls=(
                    ToolCall("same", "first", {}),
                    ToolCall("same", "second", {}),
                )
            ),
            ValueError,
            "must be unique",
        ),
        (
            lambda: ToolResultMessage("", "result"),
            ValueError,
            "call_id",
        ),
        (
            lambda: TextCompletion(""),
            ValueError,
            "completion content",
        ),
    ],
)
def test_tool_contract_rejects_invalid_states(factory, error_type, message) -> None:
    with pytest.raises(error_type, match=message):
        factory()
