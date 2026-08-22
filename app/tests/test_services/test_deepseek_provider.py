import json

import httpx
import pytest

from app.core.exceptions import (
    LLMConfigurationError,
    LLMResponseFormatError,
    LLMStreamError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.contracts import LLMMessage, LLMRole
from app.llm.providers.deepseek import DeepSeekConfig, DeepSeekProvider

TEST_CONFIG = DeepSeekConfig(
    base_url="https://llm.test",
    api_key="test-api-key",
    model="test-model",
    max_tokens=2048,
)
TEST_MESSAGES = [
    LLMMessage(role=LLMRole.SYSTEM, content="system prompt"),
    LLMMessage(role=LLMRole.USER, content="你好"),
]
TEST_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "sentiment": {"type": "string"},
    },
    "required": ["topic", "sentiment"],
    "additionalProperties": False,
}


def _stream_client(body: str, status_code: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.test/chat/completions"
        return httpx.Response(status_code, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_config_from_env_fails_closed_when_required_value_is_missing() -> None:
    with pytest.raises(LLMConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekConfig.from_env(
            {
                "DEEPSEEK_BASE_URL": "https://llm.test",
                "DEEPSEEK_MODEL": "test-model",
                "LLM_MAX_OUTPUT_TOKENS": "2048",
            }
        )


async def test_complete_translates_contract_to_provider_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer test-api-key"
        assert payload["model"] == "test-model"
        assert payload["messages"] == [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "你好"},
        ]
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["max_tokens"] == 2048
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "模型回复"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        assert await provider.complete(TEST_MESSAGES) == "模型回复"


async def test_complete_wraps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive timeout detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMTimeoutError, match="request timeout"):
            await provider.complete(TEST_MESSAGES)


async def test_complete_wraps_malformed_response() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    ) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMResponseFormatError):
            await provider.complete(TEST_MESSAGES)


async def test_complete_upstream_error_does_not_expose_response_body() -> None:
    sensitive_body = "secret upstream diagnostic and user content"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=sensitive_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMUpstreamError) as error:
            await provider.complete(TEST_MESSAGES)

    assert "503" in str(error.value)
    assert sensitive_body not in str(error.value)


async def test_complete_structured_returns_validated_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["max_tokens"] == TEST_CONFIG.max_tokens
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"topic": "AI", "sentiment": "positive"}'
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        result = await provider.complete_structured(TEST_MESSAGES, TEST_SCHEMA)

    assert result == {"topic": "AI", "sentiment": "positive"}


async def test_complete_structured_rejects_invalid_json() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not-json"}}]},
            )
        )
    ) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMResponseFormatError, match="valid JSON object"):
            await provider.complete_structured(TEST_MESSAGES, TEST_SCHEMA)


async def test_complete_structured_rejects_schema_mismatch() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"topic": "AI"}'}}]},
            )
        )
    ) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMResponseFormatError, match="does not match schema"):
            await provider.complete_structured(TEST_MESSAGES, TEST_SCHEMA)


async def test_complete_structured_rejects_empty_content() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": ""}}]},
            )
        )
    ) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMResponseFormatError, match="valid JSON object"):
            await provider.complete_structured(TEST_MESSAGES, TEST_SCHEMA)


async def test_complete_structured_rejects_invalid_schema_before_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called for invalid schema")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMConfigurationError, match="schema is invalid"):
            await provider.complete_structured(
                TEST_MESSAGES,
                {"type": "not-a-valid-type"},
            )


async def test_complete_structured_upstream_error_does_not_expose_body() -> None:
    sensitive_body = "secret structured upstream body"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=sensitive_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMUpstreamError) as error:
            await provider.complete_structured(TEST_MESSAGES, TEST_SCHEMA)

    assert "503" in str(error.value)
    assert sensitive_body not in str(error.value)


async def test_stream_yields_chunks_when_done_marker_is_received() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"第一段"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"第二段"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    async with _stream_client(body) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        chunks = [chunk async for chunk in provider.stream(TEST_MESSAGES)]

    assert chunks == ["第一段", "第二段"]


async def test_stream_wraps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive timeout detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMTimeoutError, match="stream request timeout"):
            async for _ in provider.stream(TEST_MESSAGES):
                pass


async def test_stream_raises_when_connection_ends_without_done() -> None:
    body = 'data: {"choices":[{"delta":{"content":"半截回复"}}]}\n\n'
    chunks: list[str] = []

    async with _stream_client(body) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMStreamError, match=r"before \[DONE\]"):
            async for chunk in provider.stream(TEST_MESSAGES):
                chunks.append(chunk)

    assert chunks == ["半截回复"]


@pytest.mark.parametrize(
    "body",
    [
        "data: {not-json}\n\ndata: [DONE]\n\n",
        "data: {}\n\ndata: [DONE]\n\n",
        'data: {"choices":[{"delta":null}]}\n\ndata: [DONE]\n\n',
    ],
)
async def test_stream_wraps_malformed_sse_as_format_error(body: str) -> None:
    async with _stream_client(body) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMResponseFormatError):
            async for _ in provider.stream(TEST_MESSAGES):
                pass


async def test_stream_upstream_error_does_not_expose_response_body() -> None:
    sensitive_body = "secret upstream diagnostic and user content"

    async with _stream_client(sensitive_body, status_code=503) as client:
        provider = DeepSeekProvider(client, TEST_CONFIG)
        with pytest.raises(LLMUpstreamError) as error:
            async for _ in provider.stream(TEST_MESSAGES):
                pass

    assert "503" in str(error.value)
    assert sensitive_body not in str(error.value)
