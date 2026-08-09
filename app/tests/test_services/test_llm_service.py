import httpx
import pytest

from app.core.exceptions import LLMConfigurationError
from app.services import llm_service


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
