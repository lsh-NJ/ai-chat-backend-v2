"""Provider-neutral 的有界工具调用状态机。"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from app.llm.contracts import (
    LLMMessage,
    LLMRole,
    TextCompletion,
    ToolCallingProvider,
    ToolCallRequest,
    ToolConversationMessage,
    ToolResultMessage,
)
from app.tools.exceptions import (
    ToolArgumentsValidationError,
    ToolConfigurationError,
    ToolExecutionError,
    ToolLoopLimitError,
    ToolNotFoundError,
    ToolProtocolError,
    ToolResourceUnavailableError,
    ToolTimeoutError,
)
from app.tools.execution import ToolExecutionContext, ToolExecutor

FINALIZATION_INSTRUCTION = (
    "工具调用预算已经耗尽。只能根据当前对话和已有工具结果回答；"
    "如果信息不足，请明确说明资料不足。不得假设未获得的数据，"
    "也不得请求或声称已经执行其他工具。"
)

_SAFE_ERROR_CODES = {
    ToolNotFoundError: "unknown_tool",
    ToolArgumentsValidationError: "invalid_arguments",
    ToolResourceUnavailableError: "resource_unavailable",
    ToolTimeoutError: "tool_timeout",
    ToolExecutionError: "tool_execution_failed",
}


@dataclass(frozen=True, slots=True)
class ToolLoopPolicy:
    max_tool_rounds: int
    max_tool_calls: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("max_tool_rounds", self.max_tool_rounds),
            ("max_tool_calls", self.max_tool_calls),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ToolConfigurationError(
                    f"{field_name} must be a positive integer"
                )


def _validate_history(messages: Sequence[ToolConversationMessage]) -> set[str]:
    """验证既有 request/result 因果链，并返回已经使用的 call ID。"""

    seen: set[str] = set()
    pending: set[str] = set()
    for message in messages:
        if isinstance(message, ToolCallRequest):
            if pending:
                raise ToolProtocolError("new message interrupted tool results")
            call_ids = {call.call_id for call in message.tool_calls}
            if call_ids & seen:
                raise ToolProtocolError("tool call_id was reused in history")
            seen.update(call_ids)
            pending.update(call_ids)
        elif isinstance(message, ToolResultMessage):
            if message.call_id not in pending:
                raise ToolProtocolError("tool result has no matching request")
            pending.remove(message.call_id)
        elif pending:
            raise ToolProtocolError("new message interrupted tool results")

    if pending:
        raise ToolProtocolError("tool history contains incomplete calls")
    return seen


def _safe_error_result(call_id: str, error: Exception) -> ToolResultMessage:
    for error_type, code in _SAFE_ERROR_CODES.items():
        if isinstance(error, error_type):
            return ToolResultMessage(
                call_id=call_id,
                content=json.dumps(
                    {"ok": False, "error": {"code": code}},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
    raise TypeError("unsupported safe tool error")


class ToolOrchestrator:
    """组合 Provider 与 Executor，并强制工具预算和消息状态转换。"""

    def __init__(
        self,
        provider: ToolCallingProvider,
        executor: ToolExecutor,
        policy: ToolLoopPolicy,
    ) -> None:
        self._provider = provider
        self._executor = executor
        self._policy = policy

    async def _finalize(
        self,
        history: Sequence[ToolConversationMessage],
    ) -> TextCompletion:
        response = await self._provider.complete_with_tools(
            [
                *history,
                LLMMessage(
                    role=LLMRole.SYSTEM,
                    content=FINALIZATION_INSTRUCTION,
                ),
            ],
            (),
        )
        if isinstance(response, TextCompletion):
            return response
        raise ToolLoopLimitError(
            "provider requested tools during forced finalization"
        )

    async def run(
        self,
        messages: Sequence[ToolConversationMessage],
        context: ToolExecutionContext,
    ) -> TextCompletion:
        history = list(messages)
        seen_call_ids = _validate_history(history)
        tool_rounds = 0
        tool_calls = 0

        while True:
            if (
                tool_rounds >= self._policy.max_tool_rounds
                or tool_calls >= self._policy.max_tool_calls
            ):
                return await self._finalize(history)

            response = await self._provider.complete_with_tools(
                history,
                self._executor.definitions,
            )
            if isinstance(response, TextCompletion):
                return response

            response_ids = {call.call_id for call in response.tool_calls}
            if response_ids & seen_call_ids:
                raise ToolProtocolError("tool call_id was reused")
            if tool_calls + len(response.tool_calls) > self._policy.max_tool_calls:
                return await self._finalize(history)

            seen_call_ids.update(response_ids)
            history.append(response)
            for call in response.tool_calls:
                try:
                    result = await self._executor.execute(call, context)
                except tuple(_SAFE_ERROR_CODES) as exc:
                    result = _safe_error_result(call.call_id, exc)
                history.append(result)

            tool_rounds += 1
            tool_calls += len(response.tool_calls)
