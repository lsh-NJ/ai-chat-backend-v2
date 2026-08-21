import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy import text

import app.models.user  # noqa: F401  # 注册模型，避免运行期 Mapper 配置失败
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.user import router as user_router
from app.db.redis import close_redis, create_redis_client
from app.db.session import close_db, engine
from app.llm.contracts import LLMProvider
from app.llm.providers.deepseek import DeepSeekConfig, DeepSeekProvider

REDIS_URL = os.environ["REDIS_URL"]

def create_app(llm_provider: LLMProvider | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 生产运行必须在启动阶段完成配置校验；测试只能显式传入 fake。
        provider_config = (
            DeepSeekConfig.from_env() if llm_provider is None else None
        )

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
    return application


app = create_app()
