from contextlib import asynccontextmanager

import httpx

from app.core.exceptions import LLMResponseFormatError
from app.core.security import create_access_token
from app.db.session import AsyncSessionFactory, get_db
from app.llm.context import ContextSelector
from app.llm.tokenization import ContextBudget
from app.main import create_app
from app.tests.fakes import ContentLengthTokenCounter


class UnsupportedProvider:
    """满足 LLMProvider 但不满足 StructuredOutputProvider 的 provider。"""

    async def complete(self, messages) -> str:
        return "text"

    async def stream(self, messages):
        yield "text"


@asynccontextmanager
async def _client_with_provider(provider):
    test_app = create_app(
        llm_provider=provider,
        context_selector=ContextSelector(
            ContentLengthTokenCounter(),
            ContextBudget(context_window=1_000_000, output_reserve=1),
        ),
    )

    async def override_get_db():
        async with AsyncSessionFactory() as session:
            yield session

    test_app.dependency_overrides[get_db] = override_get_db
    async with test_app.router.lifespan_context(test_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            yield client
    test_app.dependency_overrides.clear()


async def test_structured_extract_returns_view_model(
    client,
    auth_headers,
):
    response = await client.post(
        "/structured/extract",
        json={"text": "AI is great"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] > 0
    assert body["topic"] == "AI"
    assert body["sentiment"] == "positive"


async def test_structured_extract_returns_501_when_provider_not_supported(
    fresh_schema,
    create_test_user,
):
    user = await create_test_user("unsupported-provider")
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}

    async with _client_with_provider(UnsupportedProvider()) as client:
        response = await client.post(
            "/structured/extract",
            json={"text": "AI is great"},
            headers=headers,
        )

    assert response.status_code == 501
    assert "不支持结构化输出" in response.json()["detail"]


async def test_structured_extract_maps_format_error_to_502(
    client,
    auth_headers,
    llm_provider,
):
    async def fail_structured(messages, schema):
        raise LLMResponseFormatError("bad json")

    llm_provider.structured_handler = fail_structured

    response = await client.post(
        "/structured/extract",
        json={"text": "AI is great"},
        headers=auth_headers,
    )

    assert response.status_code == 502
    assert "bad json" in response.json()["detail"]
