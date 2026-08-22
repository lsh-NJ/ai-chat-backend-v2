"""面向应用的 LLM 能力契约。

本模块刻意不包含 HTTP、环境变量或任何供应商专属细节。
应用服务依赖这些类型；具体 adapter 依赖并实现本契约，再把契约翻译成上游协议。
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


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
