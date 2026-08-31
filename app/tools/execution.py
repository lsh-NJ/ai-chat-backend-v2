"""Provider-neutral 的工具白名单与受控执行入口。"""

import asyncio
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeAlias

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.llm.contracts import (
    ToolCall,
    ToolDefinition,
    ToolResultMessage,
)
from app.tools.exceptions import (
    ToolArgumentsValidationError,
    ToolConfigurationError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
)


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """由可信应用边界注入、不能来自模型 arguments 的执行身份。"""

    user_id: int

    def __post_init__(self) -> None:
        if isinstance(self.user_id, bool) or not isinstance(self.user_id, int):
            raise TypeError("tool context user_id must be an integer")
        if self.user_id <= 0:
            raise ValueError("tool context user_id must be positive")


ToolResult: TypeAlias = Mapping[str, Any]
ToolHandler: TypeAlias = Callable[
    [Mapping[str, Any], ToolExecutionContext],
    Awaitable[ToolResult],
]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """把提供给模型的工具定义与应用内部 handler 固定绑定。"""

    definition: ToolDefinition
    handler: ToolHandler

    def __post_init__(self) -> None:
        if not isinstance(self.definition, ToolDefinition):
            raise TypeError("registered tool definition must be ToolDefinition")
        if not callable(self.handler):
            raise TypeError("registered tool handler must be callable")


class ToolRegistry:
    """由应用显式构造、之后不可动态增加能力的工具白名单。"""

    def __init__(self, tools: Sequence[RegisteredTool]) -> None:
        registered: dict[str, RegisteredTool] = {}
        for tool in tools:
            if not isinstance(tool, RegisteredTool):
                raise TypeError("registry entries must be RegisteredTool")
            name = tool.definition.name
            if name in registered:
                raise ToolConfigurationError("duplicate registered tool name")
            try:
                Draft202012Validator.check_schema(
                    dict(tool.definition.parameters)
                )
            except SchemaError as exc:
                raise ToolConfigurationError(
                    "registered tool schema is invalid"
                ) from exc
            registered[name] = tool

        self._tools: Mapping[str, RegisteredTool] = MappingProxyType(registered)

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        """只向 provider 暴露公开定义，不暴露 handler。"""

        return tuple(tool.definition for tool in self._tools.values())

    def resolve(self, name: str) -> RegisteredTool:
        """只允许 Executor 从显式白名单精确解析注册项。"""

        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError("tool is not registered") from exc


class ToolExecutor:
    """所有真实工具调用必须经过的固定安全执行路径。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise ToolConfigurationError("tool timeout must be a number")
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise ToolConfigurationError("tool timeout must be finite and positive")
        self._registry = registry
        self._timeout_seconds = float(timeout_seconds)

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        """返回与执行白名单来自同一 Registry 的公开工具定义。"""

        return self._registry.definitions

    async def execute(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolResultMessage:
        registered = self._registry.resolve(call.name)
        try:
            Draft202012Validator(
                dict(registered.definition.parameters)
            ).validate(dict(call.arguments))
        except ValidationError as exc:
            raise ToolArgumentsValidationError(
                "tool arguments do not match registered schema"
            ) from exc

        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await registered.handler(call.arguments, context)
        except TimeoutError as exc:
            raise ToolTimeoutError("tool execution timed out") from exc
        except ToolError:
            raise
        except Exception as exc:
            raise ToolExecutionError("tool handler failed") from exc

        if not isinstance(result, Mapping) or any(
            not isinstance(key, str) for key in result
        ):
            raise ToolExecutionError("tool result must be a JSON object")
        try:
            content = json.dumps(
                dict(result),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError("tool result is not JSON serializable") from exc

        return ToolResultMessage(call_id=call.call_id, content=content)
