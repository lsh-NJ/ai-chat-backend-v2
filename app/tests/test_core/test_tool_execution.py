import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from app.llm.contracts import ToolCall, ToolDefinition, ToolResultMessage
from app.tools.exceptions import (
    ToolArgumentsValidationError,
    ToolConfigurationError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolResourceUnavailableError,
    ToolTimeoutError,
)
from app.tools.execution import (
    RegisteredTool,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)

TEST_DEFINITION = ToolDefinition(
    name="lookup",
    description="读取一项资源",
    parameters={
        "type": "object",
        "properties": {"resource_id": {"type": "integer", "minimum": 1}},
        "required": ["resource_id"],
        "additionalProperties": False,
    },
)


class RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[Mapping[str, Any], ToolExecutionContext]] = []

    async def __call__(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        self.calls.append((arguments, context))
        return {"z": "中文", "a": arguments["resource_id"]}


def _executor(handler: RecordingHandler) -> ToolExecutor:
    registry = ToolRegistry(
        [RegisteredTool(definition=TEST_DEFINITION, handler=handler)]
    )
    return ToolExecutor(registry, timeout_seconds=1)


def test_registry_exposes_definitions_in_registration_order() -> None:
    second = ToolDefinition("second", "第二项", {"type": "object"})
    handler = RecordingHandler()
    registry = ToolRegistry(
        [
            RegisteredTool(TEST_DEFINITION, handler),
            RegisteredTool(second, handler),
        ]
    )

    assert registry.definitions == (TEST_DEFINITION, second)


def test_registry_rejects_duplicate_names() -> None:
    handler = RecordingHandler()
    with pytest.raises(ToolConfigurationError, match="duplicate"):
        ToolRegistry(
            [
                RegisteredTool(TEST_DEFINITION, handler),
                RegisteredTool(TEST_DEFINITION, handler),
            ]
        )


def test_registry_rejects_invalid_schema_at_registration() -> None:
    invalid = ToolDefinition(
        "broken",
        "非法 schema",
        {"type": "not-a-json-schema-type"},
    )
    with pytest.raises(ToolConfigurationError, match="schema is invalid"):
        ToolRegistry([RegisteredTool(invalid, RecordingHandler())])


async def test_executor_returns_deterministic_result_with_original_call_id() -> None:
    handler = RecordingHandler()
    context = ToolExecutionContext(user_id=7)

    result = await _executor(handler).execute(
        ToolCall("call-1", "lookup", {"resource_id": 42}),
        context,
    )

    assert result == ToolResultMessage(
        call_id="call-1",
        content='{"a":42,"z":"中文"}',
    )
    assert handler.calls == [({"resource_id": 42}, context)]


async def test_unknown_tool_never_reaches_handler() -> None:
    handler = RecordingHandler()
    with pytest.raises(ToolNotFoundError, match="not registered"):
        await _executor(handler).execute(
            ToolCall("call-1", "unknown", {}),
            ToolExecutionContext(user_id=7),
        )

    assert handler.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"resource_id": 0},
        {"resource_id": "1"},
        {"resource_id": 1, "user_id": 999},
    ],
)
async def test_invalid_arguments_never_reach_handler(
    arguments: dict[str, object],
) -> None:
    handler = RecordingHandler()
    with pytest.raises(ToolArgumentsValidationError, match="registered schema"):
        await _executor(handler).execute(
            ToolCall("call-1", "lookup", arguments),
            ToolExecutionContext(user_id=7),
        )

    assert handler.calls == []


async def test_timeout_is_safe_and_distinct() -> None:
    async def blocked_handler(
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        await asyncio.Event().wait()
        return {}

    registry = ToolRegistry([RegisteredTool(TEST_DEFINITION, blocked_handler)])
    executor = ToolExecutor(registry, timeout_seconds=0.001)

    with pytest.raises(ToolTimeoutError, match="timed out"):
        await executor.execute(
            ToolCall("call-1", "lookup", {"resource_id": 1}),
            ToolExecutionContext(user_id=7),
        )


async def test_resource_unavailable_error_remains_distinct() -> None:
    async def unavailable_handler(
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        raise ToolResourceUnavailableError("resource is unavailable")

    executor = ToolExecutor(
        ToolRegistry([RegisteredTool(TEST_DEFINITION, unavailable_handler)]),
        timeout_seconds=1,
    )

    with pytest.raises(ToolResourceUnavailableError, match="unavailable"):
        await executor.execute(
            ToolCall("call-1", "lookup", {"resource_id": 1}),
            ToolExecutionContext(user_id=7),
        )


async def test_handler_failure_does_not_expose_internal_detail() -> None:
    secret = "database password and private message"

    async def failing_handler(
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        raise RuntimeError(secret)

    executor = ToolExecutor(
        ToolRegistry([RegisteredTool(TEST_DEFINITION, failing_handler)]),
        timeout_seconds=1,
    )

    with pytest.raises(ToolExecutionError) as error:
        await executor.execute(
            ToolCall("call-1", "lookup", {"resource_id": 1}),
            ToolExecutionContext(user_id=7),
        )

    assert secret not in str(error.value)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_executor_rejects_invalid_timeout(timeout: float) -> None:
    with pytest.raises(ToolConfigurationError):
        ToolExecutor(ToolRegistry([]), timeout_seconds=timeout)
