import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text

from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.auth import router as auth_router
from app.db.session import engine, close_db
import app.models.user  # noqa: F401  注册 User 模型，避免运行期 Mapper 配置失败


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

    timeout = httpx.Timeout(
        connect=10,
        read=60,
        write=30,
        pool=10,
    )

    limits = httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
    ) as client:
        app.state.http_client = client
        yield
        await close_db()


# 创建 app 对象，后面是后端应用本体
app = FastAPI(
    title="AI Chat Backend",
    description="一个由 FastAPI 实现的简单 AI 后端",
    lifespan=lifespan,
)

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(auth_router)
