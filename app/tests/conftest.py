import os
import httpx
from pathlib import Path
from sqlalchemy import text
import asyncio
from dotenv import load_dotenv
from alembic import command
from alembic.config import Config

load_dotenv()
os.environ["POSTGRES_DB"] = os.environ["POSTGRES_TEST_DB"]

if not os.environ["POSTGRES_DB"].endswith("_test"):
    raise RuntimeError(
        f"测试库名必须以 _test 结尾，当前是 {os.environ['POSTGRES_DB']}"
    )


import pytest  # noqa: E402

from app.main import app
from app.db.base import Base  # noqa: E402
from app.db.session import AsyncSessionFactory, engine, get_db  # noqa: E402
from app.models.user import User  # noqa: E402


def _upgrade_head() -> None:
    # 绝对路径，不依赖“当前目录刚好是项目根目录”
    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini_path))
    command.upgrade(cfg, "head")


@pytest.fixture
async def fresh_schema():
    async with engine.begin() as conn:
        # 清空整库，模拟“空数据库”
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await asyncio.to_thread(_upgrade_head)
    yield


@pytest.fixture
async def client(fresh_schema):
    async def override_get_db():
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as test_client:
            yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
async def test_user_id(fresh_schema) -> int:
    """为 Repository/Service 测试创建明确的资源所有者。"""
    async with AsyncSessionFactory() as session:
        user = User(
            username="test-owner",
            password_hash=(
                "$2b$12$D6pRJxwBIEpO4eR/l/7OVOmJotevDNKnZxC2.0ebLckx9bpookcpu"
            ),
        )
        session.add(user)
        await session.commit()
        return user.id


@pytest.fixture
async def auth_headers(client) -> dict[str, str]:
    """通过真实注册、登录接口取得 Bearer token。"""
    register_response = await client.post(
        "/auth/register",
        json={"username": "api-user", "password": "88888888"},
    )
    assert register_response.status_code == 201

    login_response = await client.post(
        "/auth/login",
        data={"username": "api-user", "password": "88888888"},
    )
    assert login_response.status_code == 200

    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
