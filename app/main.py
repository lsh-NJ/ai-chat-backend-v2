import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy import text

import app.models.user  # noqa: F401  # 注册模型，避免运行期 Mapper 配置失败
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.structured import router as structured_router
from app.api.user import router as user_router
from app.core.exceptions import LLMConfigurationError
from app.db.redis import close_redis, create_redis_client
from app.db.session import close_db, engine
from app.llm.context import ContextSelector
from app.llm.contracts import LLMProvider
from app.llm.deepseek_v4_tokenizer import DeepSeekV4TokenCounter
from app.llm.providers.deepseek import DeepSeekConfig, DeepSeekProvider
from app.llm.tokenization import ContextBudget

REDIS_URL = os.environ["REDIS_URL"]

def _read_non_negative_int(name: str, *, positive: bool = False) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        raise LLMConfigurationError(f"缺少 LLM 配置环境变量: {name}")
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise LLMConfigurationError(f"{name} 必须是整数") from exc
    if value < 0 or (positive and value == 0):
        constraint = "正整数" if positive else "非负整数"
        raise LLMConfigurationError(f"{name} 必须是{constraint}")
    return value


def create_app(
    llm_provider: LLMProvider | None = None,
    context_selector: ContextSelector | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 生产运行必须在启动阶段完成配置校验；测试只能显式传入 fake。
        provider_config = (
            DeepSeekConfig.from_env() if llm_provider is None else None
        )
        if (llm_provider is None) != (context_selector is None):
            raise LLMConfigurationError(
                "provider 与 context selector 必须同时由测试显式注入"
            )

        runtime_selector = context_selector
        if runtime_selector is None:
            assert provider_config is not None
            counter = DeepSeekV4TokenCounter.from_resource(
                model=provider_config.model
            )
            try:
                budget = ContextBudget(
                    context_window=_read_non_negative_int(
                        "LLM_CONTEXT_WINDOW",
                        positive=True,
                    ),
                    output_reserve=provider_config.max_tokens,
                    safety_margin=_read_non_negative_int(
                        "LLM_TOKEN_SAFETY_MARGIN"
                    ),
                )
            except (TypeError, ValueError) as exc:
                raise LLMConfigurationError("LLM 上下文预算配置非法") from exc
            runtime_selector = ContextSelector(counter, budget)

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

        timeout = httpx.Timeout(connect=10, read=60, write=30, pool=10)
        limits = httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        )
        redis = create_redis_client(REDIS_URL)

        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            try:
                await redis.ping()
                if llm_provider is not None:
                    app.state.llm_provider = llm_provider
                else:
                    assert provider_config is not None
                    app.state.llm_provider = DeepSeekProvider(
                        client,
                        provider_config,
                    )
                app.state.redis = redis
                app.state.context_selector = runtime_selector
                yield
            finally:
                await close_redis(redis)
                await close_db()

    application = FastAPI(
        title="AI Chat Backend",
        description="一个由 FastAPI 实现的简单 AI 后端",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(chat_router)
    application.include_router(conversations_router)
    application.include_router(auth_router)
    application.include_router(user_router)
    application.include_router(structured_router)
    return application


app = create_app()
