"""DeepSeek-compatible HTTP adapter for the application LLM contract."""

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass

import httpx

from app.core.exceptions import (
    LLMConfigurationError,
    LLMResponseFormatError,
    LLMStreamError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.contracts import LLMMessage


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

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        try:
            response = await self._client.post(
                self._url,
                headers={**self._headers, "Accept": "application/json"},
                json=self._payload(messages),
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("LLM content must be a string")
            return content
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("LLM request timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMUpstreamError(
                f"LLM API returned status {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMUpstreamError(
                f"LLM API request failed: {type(exc).__name__}"
            ) from exc
        except (
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise LLMResponseFormatError(
                "Unexpected LLM API response format"
            ) from exc

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
                        f"LLM API returned status {response.status_code}"
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
