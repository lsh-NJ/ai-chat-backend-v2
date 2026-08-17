import httpx
import pytest

from app.core.exceptions import (
    LLMConfigurationError,
    LLMResponseFormatError,
    LLMStreamError,
)
from app.services import llm_service


def _set_llm_config(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://llm.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")


def _stream_client(body: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.test/chat/completions"
        return httpx.Response(200, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# 目标：缺少 API Key 时，普通调用抛 LLMConfigurationError
async def test_call_llm_missing_api_key_raises_configuration_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    async with httpx.AsyncClient() as client:
        with pytest.raises(LLMConfigurationError):
            await llm_service.call_llm(
                client=client,
                messages=[{"role": "user", "content": "你好"}],
            )


# 目标：缺少 API Key 时，流式调用抛 LLMConfigurationError
async def test_stream_llm_missing_api_key_raises_configuration_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    async with httpx.AsyncClient() as client:
        with pytest.raises(LLMConfigurationError):
            async for _ in llm_service.stream_llm(
                client=client,
                messages=[{"role": "user", "content": "你好"}],
            ):
                pass


# 目标：只有收到协议的 [DONE] 才把流视为正常完成
async def test_stream_llm_yields_chunks_when_done_marker_is_received(monkeypatch):
    _set_llm_config(monkeypatch)
    body = (
        'data: {"choices":[{"delta":{"content":"第一段"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"第二段"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    async with _stream_client(body) as client:
        chunks = [
            chunk
            async for chunk in llm_service.stream_llm(
                client=client,
                messages=[{"role": "user", "content": "你好"}],
            )
        ]

    assert chunks == ["第一段", "第二段"]


# 目标：上游在 [DONE] 前 EOF 时不能误标为完整
async def test_stream_llm_raises_when_connection_ends_without_done(monkeypatch):
    _set_llm_config(monkeypatch)
    body = 'data: {"choices":[{"delta":{"content":"半截回复"}}]}\n\n'
    chunks: list[str] = []

    async with _stream_client(body) as client:
        with pytest.raises(LLMStreamError, match=r"before \[DONE\]"):
            async for chunk in llm_service.stream_llm(
                client=client,
                messages=[{"role": "user", "content": "你好"}],
            ):
                chunks.append(chunk)

    assert chunks == ["半截回复"]


@pytest.mark.parametrize(
    "body",
    [
        "data: {not-json}\n\ndata: [DONE]\n\n",
        "data: {}\n\ndata: [DONE]\n\n",
        "data: {\"choices\":[{\"delta\":null}]}\n\ndata: [DONE]\n\n",
    ],
)
async def test_stream_llm_wraps_malformed_sse_as_format_error(monkeypatch, body):
    _set_llm_config(monkeypatch)

    async with _stream_client(body) as client:
        with pytest.raises(LLMResponseFormatError):
            async for _ in llm_service.stream_llm(
                client=client,
                messages=[{"role": "user", "content": "你好"}],
            ):
                pass
