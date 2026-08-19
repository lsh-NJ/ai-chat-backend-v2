import json
import os
from collections.abc import AsyncIterator

import httpx

from app.core.exceptions import (
    LLMConfigurationError,
    LLMResponseFormatError,
    LLMStreamError,
    LLMTimeoutError,
    LLMUpstreamError,
)


def _get_llm_config() -> dict[str, str]:
    """读取并校验 LLM 配置；缺失或为空时抛 LLMConfigurationError。"""
    required = ("DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise LLMConfigurationError(
            "缺少 LLM 配置环境变量: " + ", ".join(missing)
        )

    return {
        "base_url": os.environ["DEEPSEEK_BASE_URL"],
        "api_key": os.environ["DEEPSEEK_API_KEY"],
        "model": os.environ["DEEPSEEK_MODEL"],
    }


async def call_llm(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
) -> str:
    config = _get_llm_config()

    url = f"{config['base_url']}/chat/completions"

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "system",
                "content": "你是一个简洁、友好、可靠的 AI 助手。",
            },
            *messages,
        ],
        "temperature": 0.7,
        "thinking": {
            "type": "disabled",
        },
    }

    try:
        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

        response.raise_for_status()
        data = response.json()

        return data["choices"][0]["message"]["content"]

    except httpx.TimeoutException as e:
        raise LLMTimeoutError(
            "LLM request timeout",
        ) from e

    except httpx.HTTPStatusError as e:
        raise LLMUpstreamError(
            f"LLM API returned status {e.response.status_code}"
        ) from e

    except httpx.RequestError as e:
        raise LLMUpstreamError(
            f"LLM API request failed: {type(e).__name__}",
        ) from e

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

    
# 流式输出
async def stream_llm(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
) -> AsyncIterator[str]:
    config = _get_llm_config()

    url = f"{config['base_url']}/chat/completions"

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "system",
                "content": "你是一个简洁、友好、可靠的 AI 助手。",
            },
            *messages,
        ],
        "temperature": 0.7,
        "thinking": {
            "type": "disabled",
        },
        "stream": True,
    }

    try:
        async with client.stream(
            method="POST",
            url=url,
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise LLMUpstreamError(body.decode(errors="replace"))

            received_done = False
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    received_done = True
                    break
                event_payload = json.loads(data)
                delta = event_payload["choices"][0]["delta"].get("content")
                if delta:
                    yield delta

            if not received_done:
                raise LLMStreamError("LLM stream ended before [DONE]")

    except httpx.TimeoutException as exc:
        raise LLMTimeoutError(
            "LLM stream request timeout"
        ) from exc

    except httpx.HTTPStatusError as exc:
        raise LLMUpstreamError(
            f"LLM API returned status {exc.response.status_code}"
        ) from exc

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
