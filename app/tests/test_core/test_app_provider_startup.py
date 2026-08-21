import pytest

from app.core.exceptions import LLMConfigurationError
from app.main import create_app


async def test_production_app_fails_closed_when_provider_config_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    production_app = create_app()

    with pytest.raises(LLMConfigurationError, match="DEEPSEEK_API_KEY"):
        async with production_app.router.lifespan_context(production_app):
            pass
