"""面向应用 LLM 契约的 DeepSeek 兼容 HTTP adapter。"""

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.core.exceptions import (
    LLMConfigurationError,
    LLMResponseFormatError,
    LLMStreamError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.contracts import (
    JSONSchema,
    LLMMessage,
    TextCompletion,
    ToolCall,
    ToolCallingResult,
    ToolCallRequest,
    ToolConversationMessage,
    ToolDefinition,
    ToolResultMessage,
)


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    base_url: str
    api_key: str
    model: str
    max_tokens: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DeepSeekConfig":
        values = os.environ if environ is None else environ
        required = (
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_MODEL",
            "LLM_MAX_OUTPUT_TOKENS",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise LLMConfigurationError(
                "缺少 LLM 配置环境变量: " + ", ".join(missing)
            )

        try:
            max_tokens = int(values["LLM_MAX_OUTPUT_TOKENS"])
        except ValueError as exc:
            raise LLMConfigurationError(
                "LLM_MAX_OUTPUT_TOKENS 必须是正整数"
            ) from exc
        if max_tokens <= 0:
            raise LLMConfigurationError("LLM_MAX_OUTPUT_TOKENS 必须是正整数")

        return cls(
            base_url=values["DEEPSEEK_BASE_URL"].rstrip("/"),
            api_key=values["DEEPSEEK_API_KEY"],
            model=values["DEEPSEEK_MODEL"],
            max_tokens=max_tokens,
        )


class DeepSeekProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        config: DeepSeekConfig,
    ) -> None:
        self._client = client
        self._config = config

    @property
    def _url(self) -> str:
        return f"{self._config.base_url}/chat/completions"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages: Sequence[LLMMessage]) -> dict[str, object]:
        return {
            "model": self._config.model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in messages
            ],
            "temperature": 0.7,
            "thinking": {"type": "disabled"},
            "max_tokens": self._config.max_tokens,
        }

    async def _request(
        self,
        payload: Mapping[str, object],
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                self._url,
                headers={**self._headers, "Accept": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("LLM request timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMUpstreamError(
                f"LLM API returned status {exc.response.status_code}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise LLMUpstreamError(
                f"LLM API request failed: {type(exc).__name__}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LLMResponseFormatError(
                "Unexpected LLM API response format"
            ) from exc

        if not isinstance(data, dict):
            raise LLMResponseFormatError("Unexpected LLM API response format")
        return data

    @staticmethod
    def _choice_message(
        data: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        try:
            choices = data["choices"]
            if not isinstance(choices, list) or not choices:
                raise TypeError("choices must be a non-empty list")
            choice = choices[0]
            if not isinstance(choice, Mapping):
                raise TypeError("choice must be an object")
            message = choice["message"]
            if not isinstance(message, Mapping):
                raise TypeError("message must be an object")
            return choice, message
        except (KeyError, TypeError) as exc:
            raise LLMResponseFormatError(
                "Unexpected LLM API response format"
            ) from exc

    async def _request_content(
        self,
        messages: Sequence[LLMMessage],
        *,
        response_format: Mapping[str, str] | None = None,
    ) -> str:
        payload = self._payload(messages)
        if response_format is not None:
            payload = {**payload, "response_format": response_format}

        data = await self._request(payload)
        _, message = self._choice_message(data)
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMResponseFormatError(
                "Unexpected LLM API response format"
            )
        return content

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        return await self._request_content(messages)

    async def complete_structured(
        self,
        messages: Sequence[LLMMessage],
        schema: JSONSchema,
    ) -> dict[str, Any]:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise LLMConfigurationError(
                "LLM structured output schema is invalid"
            ) from exc

        content = await self._request_content(
            messages,
            response_format={"type": "json_object"},
        )
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise TypeError("LLM structured output must be a JSON object")
            Draft202012Validator(schema).validate(parsed)
            return parsed
        except ValidationError as exc:
            raise LLMResponseFormatError(
                "LLM structured response does not match schema"
            ) from exc
        except (TypeError, json.JSONDecodeError) as exc:
            raise LLMResponseFormatError(
                "LLM structured response is not a valid JSON object"
            ) from exc

    @staticmethod
    def _serialize_tool_message(
        message: ToolConversationMessage,
    ) -> dict[str, object]:
        if isinstance(message, LLMMessage):
            return {"role": message.role.value, "content": message.content}
        if isinstance(message, ToolCallRequest):
            return {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                dict(call.arguments),
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        if isinstance(message, ToolResultMessage):
            return {
                "role": "tool",
                "tool_call_id": message.call_id,
                "content": message.content,
            }
        raise TypeError("unsupported tool conversation message")

    @staticmethod
    def _serialize_tool(tool: ToolDefinition) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            },
        }

    @staticmethod
    def _parse_tool_call(raw_call: object) -> ToolCall:
        try:
            if not isinstance(raw_call, Mapping):
                raise TypeError("tool call must be an object")
            call_id = raw_call["id"]
            if raw_call.get("type") != "function":
                raise TypeError("tool call type must be function")
            function = raw_call["function"]
            if not isinstance(function, Mapping):
                raise TypeError("tool call function must be an object")
            name = function["name"]
            raw_arguments = function["arguments"]
            if not isinstance(raw_arguments, str):
                raise TypeError("tool arguments must be a string")
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be an object")
            return ToolCall(
                call_id=call_id,
                name=name,
                arguments=arguments,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise LLMResponseFormatError(
                "Unexpected LLM tool call response format"
            ) from exc

    async def complete_with_tools(
        self,
        messages: Sequence[ToolConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> ToolCallingResult:
        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                self._serialize_tool_message(message) for message in messages
            ],
            "temperature": 0.7,
            "thinking": {"type": "disabled"},
            "max_tokens": self._config.max_tokens,
        }
        if tools:
            payload["tools"] = [self._serialize_tool(tool) for tool in tools]
            payload["tool_choice"] = "auto"
        data = await self._request(payload)
        choice, message = self._choice_message(data)
        finish_reason = choice.get("finish_reason")
        raw_tool_calls = message.get("tool_calls")

        if finish_reason == "stop":
            if raw_tool_calls not in (None, []):
                raise LLMResponseFormatError(
                    "LLM tool response reason contradicts message"
                )
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise LLMResponseFormatError(
                    "LLM tool response did not contain complete text"
                )
            return TextCompletion(content=content)

        if finish_reason == "tool_calls":
            if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
                raise LLMResponseFormatError(
                    "LLM tool response did not contain tool calls"
                )
            raw_content = message.get("content")
            if raw_content is not None and not isinstance(raw_content, str):
                raise LLMResponseFormatError(
                    "Unexpected LLM tool call response format"
                )
            try:
                return ToolCallRequest(
                    tool_calls=tuple(
                        self._parse_tool_call(raw_call)
                        for raw_call in raw_tool_calls
                    ),
                    content=raw_content or "",
                )
            except (TypeError, ValueError) as exc:
                raise LLMResponseFormatError(
                    "Unexpected LLM tool call response format"
                ) from exc

        raise LLMResponseFormatError(
            "LLM tool response did not contain a complete result"
        )

    async def stream(
        self,
        messages: Sequence[LLMMessage],
    ) -> AsyncIterator[str]:
        payload = {**self._payload(messages), "stream": True}
        try:
            async with self._client.stream(
                method="POST",
                url=self._url,
                headers={**self._headers, "Accept": "text/event-stream"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    raise LLMUpstreamError(
                        f"LLM API returned status {response.status_code}",
                        status_code=response.status_code,
                    )

                received_done = False
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw_event = line.removeprefix("data:").strip()
                    if raw_event == "[DONE]":
                        received_done = True
                        break

                    event = json.loads(raw_event)
                    delta = event["choices"][0]["delta"].get("content")
                    if delta is not None and not isinstance(delta, str):
                        raise TypeError("LLM stream content must be a string")
                    if delta:
                        yield delta

                if not received_done:
                    raise LLMStreamError("LLM stream ended before [DONE]")
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("LLM stream request timeout") from exc
        except httpx.RequestError as exc:
            raise LLMUpstreamError(
                f"LLM stream request failed: {type(exc).__name__}"
            ) from exc
        except (
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise LLMResponseFormatError(
                "Unexpected LLM stream response format"
            ) from exc
