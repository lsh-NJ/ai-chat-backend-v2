"""面向应用的 LLM 能力契约。

本模块刻意不包含 HTTP、环境变量或任何供应商专属细节。
应用服务依赖这些类型；具体 adapter 依赖并实现本契约，再把契约翻译成上游协议。
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias, runtime_checkable


class LLMRole(StrEnum):
    """应用层能理解的对话角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """一条不可变的、与供应商无关的消息。"""

    role: LLMRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, LLMRole):
            raise TypeError("role must be an LLMRole")
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")


@runtime_checkable
class LLMProvider(Protocol):
    """Chat 应用服务需要的模型能力。"""

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        """返回一条完整的 assistant 回复。"""
        ...

    def stream(
        self,
        messages: Sequence[LLMMessage],
    ) -> AsyncIterator[str]:
        """返回文本块迭代器；迭代过程中失败会抛出异常。"""
        ...


# JSON Schema 对象本身与供应商无关；具体 adapter 会把它翻译成各自的
# 供应商结构化输出协议。
JSONSchema = Mapping[str, Any]


@runtime_checkable
class StructuredOutputProvider(Protocol):
    """能够返回经过校验的 JSON 对象的 provider。

    当上游需要 prompt 层面的引导时，调用方负责把 schema/指令写进 messages；
    adapter 负责协议翻译、JSON 解析和 schema 校验。
    """

    async def complete_structured(
        self,
        messages: Sequence[LLMMessage],
        schema: JSONSchema,
    ) -> dict[str, Any]:
        """返回一个通过 ``schema`` 校验的 JSON 对象。"""
        ...


def _require_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """应用允许模型提出调用的工具说明。"""

    name: str
    description: str
    parameters: JSONSchema

    def __post_init__(self) -> None:
        _require_non_empty_string(self.name, "tool name")
        _require_non_empty_string(self.description, "tool description")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("tool parameters must be a JSON Schema object")
        object.__setattr__(
            self,
            "parameters",
            MappingProxyType(dict(self.parameters)),
        )


@dataclass(frozen=True, slots=True)
class ToolCall:
    """adapter 已解析为 JSON object 的一次模型工具调用建议。"""

    call_id: str
    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.call_id, "tool call_id")
        _require_non_empty_string(self.name, "tool call name")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("tool arguments must be a JSON object")
        if any(not isinstance(key, str) for key in self.arguments):
            raise TypeError("tool argument keys must be strings")
        object.__setattr__(
            self,
            "arguments",
            MappingProxyType(dict(self.arguments)),
        )


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """需要写入历史并等待对应 tool result 的 assistant 消息。"""

    tool_calls: tuple[ToolCall, ...]
    content: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("tool call request content must be a string")
        if not isinstance(self.tool_calls, tuple):
            raise TypeError("tool_calls must be a tuple")
        if not self.tool_calls:
            raise ValueError("tool_calls must not be empty")
        if any(not isinstance(call, ToolCall) for call in self.tool_calls):
            raise TypeError("tool_calls must contain ToolCall values")
        call_ids = [call.call_id for call in self.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool call_ids must be unique within one response")


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    """应用执行工具后，按 call_id 回传给模型的结果消息。"""

    call_id: str
    content: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.call_id, "tool result call_id")
        if not isinstance(self.content, str):
            raise TypeError("tool result content must be a string")


@dataclass(frozen=True, slots=True)
class TextCompletion:
    """Tool Calling 状态机的最终文本结果。"""

    content: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.content, "completion content")


ToolConversationMessage: TypeAlias = (
    LLMMessage | ToolCallRequest | ToolResultMessage
)
ToolCallingResult: TypeAlias = TextCompletion | ToolCallRequest


@runtime_checkable
class ToolCallingProvider(Protocol):
    """能够返回最终文本或工具调用建议的独立 provider 能力。"""

    async def complete_with_tools(
        self,
        messages: Sequence[ToolConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> ToolCallingResult: ...
