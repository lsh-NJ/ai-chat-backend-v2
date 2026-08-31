import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.core.exceptions import LLMTimeoutError
from app.llm.contracts import (
    LLMMessage,
    LLMRole,
    TextCompletion,
    ToolCall,
    ToolCallingResult,
    ToolCallRequest,
    ToolConversationMessage,
    ToolDefinition,
    ToolResultMessage,
)
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolLoopLimitError,
    ToolProtocolError,
    ToolResourceUnavailableError,
)
from app.tools.execution import (
    RegisteredTool,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)
from app.tools.orchestrator import (
    FINALIZATION_INSTRUCTION,
    ToolLoopPolicy,
    ToolOrchestrator,
)

TEST_DEFINITION = ToolDefinition(
    "lookup",
    "读取资源",
    {
        "type": "object",
        "properties": {"resource_id": {"type": "integer", "minimum": 1}},
        "required": ["resource_id"],
        "additionalProperties": False,
    },
)
INITIAL_MESSAGES = (LLMMessage(LLMRole.USER, "读取资源"),)


class ScriptedProvider:
    def __init__(
        self,
        responses: Sequence[ToolCallingResult | Exception],
    ) -> None:
        self._responses = list(responses)
        self.calls: list[
            tuple[
                tuple[ToolConversationMessage, ...],
                tuple[ToolDefinition, ...],
            ]
        ] = []

    async def complete_with_tools(
        self,
        messages: Sequence[ToolConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> ToolCallingResult:
        self.calls.append((tuple(messages), tuple(tools)))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingHandler:
    def __init__(self) -> None:
        self.resource_ids: list[int] = []

    async def __call__(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        resource_id = arguments["resource_id"]
        assert isinstance(resource_id, int)
        self.resource_ids.append(resource_id)
        return {"resource_id": resource_id, "owner_id": context.user_id}


def _orchestrator(
    provider: ScriptedProvider,
    handler: RecordingHandler,
    *,
    max_rounds: int = 3,
    max_calls: int = 5,
    timeout_seconds: float = 1,
) -> ToolOrchestrator:
    registry = ToolRegistry([RegisteredTool(TEST_DEFINITION, handler)])
    executor = ToolExecutor(registry, timeout_seconds=timeout_seconds)
    return ToolOrchestrator(
        provider,
        executor,
        ToolLoopPolicy(max_rounds, max_calls),
    )


def _request(*calls: ToolCall) -> ToolCallRequest:
    return ToolCallRequest(tool_calls=tuple(calls))


async def test_immediate_text_does_not_execute_tool() -> None:
    provider = ScriptedProvider([TextCompletion("直接回答")])
    handler = RecordingHandler()

    result = await _orchestrator(provider, handler).run(
        INITIAL_MESSAGES,
        ToolExecutionContext(user_id=7),
    )

    assert result == TextCompletion("直接回答")
    assert handler.resource_ids == []
    assert provider.calls == [(INITIAL_MESSAGES, (TEST_DEFINITION,))]


async def test_tool_round_replays_request_then_result() -> None:
    request = _request(ToolCall("call-1", "lookup", {"resource_id": 42}))
    provider = ScriptedProvider([request, TextCompletion("最终回答")])
    handler = RecordingHandler()
    original = list(INITIAL_MESSAGES)

    result = await _orchestrator(provider, handler).run(
        original,
        ToolExecutionContext(user_id=7),
    )

    assert result == TextCompletion("最终回答")
    assert original == list(INITIAL_MESSAGES)
    second_messages, second_tools = provider.calls[1]
    assert second_messages[:-1] == (*INITIAL_MESSAGES, request)
    tool_result = second_messages[-1]
    assert isinstance(tool_result, ToolResultMessage)
    assert tool_result.call_id == "call-1"
    assert second_tools == (TEST_DEFINITION,)


async def test_multiple_calls_execute_and_replay_in_response_order() -> None:
    request = _request(
        ToolCall("call-1", "lookup", {"resource_id": 1}),
        ToolCall("call-2", "lookup", {"resource_id": 2}),
    )
    provider = ScriptedProvider([request, TextCompletion("完成")])
    handler = RecordingHandler()

    await _orchestrator(provider, handler).run(
        INITIAL_MESSAGES,
        ToolExecutionContext(user_id=7),
    )

    assert handler.resource_ids == [1, 2]
    replayed = provider.calls[1][0]
    assert [message.call_id for message in replayed[-2:]] == [
        "call-1",
        "call-2",
    ]


async def test_safe_tool_error_is_replayed_without_arguments() -> None:
    secret = "private-resource-id"
    request = _request(
        ToolCall("call-1", "lookup", {"resource_id": secret})
    )
    provider = ScriptedProvider([request, TextCompletion("无法读取")])
    handler = RecordingHandler()

    await _orchestrator(provider, handler).run(
        INITIAL_MESSAGES,
        ToolExecutionContext(user_id=7),
    )

    error_result = provider.calls[1][0][-1]
    assert isinstance(error_result, ToolResultMessage)
    assert error_result.content == (
        '{"error":{"code":"invalid_arguments"},"ok":false}'
    )
    assert secret not in error_result.content
    assert handler.resource_ids == []


async def test_unknown_tool_is_replayed_as_safe_error() -> None:
    request = _request(ToolCall("call-1", "unknown", {}))
    provider = ScriptedProvider([request, TextCompletion("工具不可用")])
    handler = RecordingHandler()

    await _orchestrator(provider, handler).run(
        INITIAL_MESSAGES,
        ToolExecutionContext(user_id=7),
    )

    error_result = provider.calls[1][0][-1]
    assert isinstance(error_result, ToolResultMessage)
    assert error_result.content == (
        '{"error":{"code":"unknown_tool"},"ok":false}'
    )
    assert handler.resource_ids == []


async def test_resource_error_is_replayed_without_internal_detail() -> None:
    secret = "private database detail"

    class UnavailableHandler(RecordingHandler):
        async def __call__(
            self,
            arguments: Mapping[str, Any],
            context: ToolExecutionContext,
        ) -> ToolResult:
            raise ToolResourceUnavailableError(secret)

    request = _request(ToolCall("call-1", "lookup", {"resource_id": 1}))
    provider = ScriptedProvider([request, TextCompletion("资源不可用")])

    await _orchestrator(provider, UnavailableHandler()).run(
        INITIAL_MESSAGES,
        ToolExecutionContext(user_id=7),
    )

    error_result = provider.calls[1][0][-1]
    assert isinstance(error_result, ToolResultMessage)
    assert error_result.content == (
        '{"error":{"code":"resource_unavailable"},"ok":false}'
    )
    assert secret not in error_result.content


async def test_handler_failure_is_replayed_without_internal_detail() -> None:
    secret = "private handler traceback detail"

    class FailingHandler(RecordingHandler):
        async def __call__(
            self,
            arguments: Mapping[str, Any],
            context: ToolExecutionContext,
        ) -> ToolResult:
            raise RuntimeError(secret)

    request = _request(ToolCall("call-1", "lookup", {"resource_id": 1}))
    provider = ScriptedProvider([request, TextCompletion("执行失败")])

    await _orchestrator(provider, FailingHandler()).run(
        INITIAL_MESSAGES,
        ToolExecutionContext(user_id=7),
    )

    error_result = provider.calls[1][0][-1]
    assert isinstance(error_result, ToolResultMessage)
    assert error_result.content == (
        '{"error":{"code":"tool_execution_failed"},"ok":false}'
    )
    assert secret not in error_result.content


async def test_timeout_is_replayed_as_safe_error() -> None:
    class BlockedHandler(RecordingHandler):
        async def __call__(
            self,
            arguments: Mapping[str, Any],
            context: ToolExecutionContext,
        ) -> ToolResult:
            await asyncio.Event().wait()
            return {}

    request = _request(ToolCall("call-1", "lookup", {"resource_id": 1}))
    provider = ScriptedProvider([request, TextCompletion("工具超时")])

    await _orchestrator(
        provider,
        BlockedHandler(),
        timeout_seconds=0.001,
    ).run(INITIAL_MESSAGES, ToolExecutionContext(user_id=7))

    error_result = provider.calls[1][0][-1]
    assert isinstance(error_result, ToolResultMessage)
    assert error_result.content == (
        '{"error":{"code":"tool_timeout"},"ok":false}'
    )


async def test_max_rounds_forces_one_no_tool_finalization() -> None:
    request = _request(ToolCall("call-1", "lookup", {"resource_id": 1}))
    provider = ScriptedProvider([request, TextCompletion("根据已有资料回答")])
    handler = RecordingHandler()

    result = await _orchestrator(
        provider,
        handler,
        max_rounds=1,
    ).run(INITIAL_MESSAGES, ToolExecutionContext(user_id=7))

    assert result == TextCompletion("根据已有资料回答")
    final_messages, final_tools = provider.calls[1]
    assert final_tools == ()
    assert isinstance(final_messages[-1], LLMMessage)
    assert final_messages[-1].role is LLMRole.SYSTEM
    assert final_messages[-1].content == FINALIZATION_INSTRUCTION
    assert handler.resource_ids == [1]


async def test_call_budget_rejects_whole_batch_then_finalizes() -> None:
    request = _request(
        ToolCall("call-1", "lookup", {"resource_id": 1}),
        ToolCall("call-2", "lookup", {"resource_id": 2}),
    )
    provider = ScriptedProvider([request, TextCompletion("资料不足")])
    handler = RecordingHandler()

    result = await _orchestrator(
        provider,
        handler,
        max_calls=1,
    ).run(INITIAL_MESSAGES, ToolExecutionContext(user_id=7))

    assert result == TextCompletion("资料不足")
    assert handler.resource_ids == []
    assert provider.calls[1][1] == ()
    assert request not in provider.calls[1][0]


async def test_forced_finalization_rejects_another_tool_request() -> None:
    first = _request(ToolCall("call-1", "lookup", {"resource_id": 1}))
    forbidden = _request(ToolCall("call-2", "lookup", {"resource_id": 2}))
    provider = ScriptedProvider([first, forbidden])
    handler = RecordingHandler()

    with pytest.raises(ToolLoopLimitError, match="forced finalization"):
        await _orchestrator(provider, handler, max_rounds=1).run(
            INITIAL_MESSAGES,
            ToolExecutionContext(user_id=7),
        )

    assert handler.resource_ids == [1]
    assert provider.calls[1][1] == ()


async def test_cross_round_duplicate_call_id_is_rejected_before_execution() -> None:
    first = _request(ToolCall("same", "lookup", {"resource_id": 1}))
    duplicate = _request(ToolCall("same", "lookup", {"resource_id": 2}))
    provider = ScriptedProvider([first, duplicate])
    handler = RecordingHandler()

    with pytest.raises(ToolProtocolError, match="reused"):
        await _orchestrator(provider, handler).run(
            INITIAL_MESSAGES,
            ToolExecutionContext(user_id=7),
        )

    assert handler.resource_ids == [1]


async def test_provider_error_propagates_without_tool_wrapping() -> None:
    provider = ScriptedProvider([LLMTimeoutError("provider timeout")])
    handler = RecordingHandler()

    with pytest.raises(LLMTimeoutError, match="provider timeout"):
        await _orchestrator(provider, handler).run(
            INITIAL_MESSAGES,
            ToolExecutionContext(user_id=7),
        )


@pytest.mark.parametrize(
    "history",
    [
        (ToolResultMessage("call-1", "{}"),),
        (
            _request(ToolCall("call-1", "lookup", {"resource_id": 1})),
        ),
        (
            _request(ToolCall("call-1", "lookup", {"resource_id": 1})),
            ToolResultMessage("call-1", "{}"),
            ToolResultMessage("call-1", "{}"),
        ),
        (
            _request(ToolCall("call-1", "lookup", {"resource_id": 1})),
            LLMMessage(LLMRole.USER, "打断工具结果"),
            ToolResultMessage("call-1", "{}"),
        ),
    ],
)
async def test_invalid_initial_tool_history_is_rejected(
    history: tuple[ToolConversationMessage, ...],
) -> None:
    provider = ScriptedProvider([TextCompletion("不应调用")])

    with pytest.raises(ToolProtocolError):
        await _orchestrator(provider, RecordingHandler()).run(
            history,
            ToolExecutionContext(user_id=7),
        )

    assert provider.calls == []


@pytest.mark.parametrize(
    ("max_rounds", "max_calls"),
    [(0, 1), (1, 0), (-1, 1), (1, -1), (True, 1), (1, True)],
)
def test_loop_policy_rejects_invalid_limits(
    max_rounds: int,
    max_calls: int,
) -> None:
    with pytest.raises(ToolConfigurationError, match="positive integer"):
        ToolLoopPolicy(max_rounds, max_calls)
