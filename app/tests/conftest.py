import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import httpx
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import text

from alembic import command

load_dotenv()
os.environ["POSTGRES_DB"] = os.environ["POSTGRES_TEST_DB"]

development_redis_url = os.environ["REDIS_URL"]
test_redis_url = os.environ["REDIS_TEST_URL"]
parsed_test_redis_url = urlparse(test_redis_url)
test_redis_db = parsed_test_redis_url.path.lstrip("/")

if (
    development_redis_url == test_redis_url
    or not test_redis_db.isdigit()
    or int(test_redis_db) != 15
):
    raise RuntimeError(
        "测试 Redis 必须使用与开发 Redis 不同的 15 号测试库，"
        f"当前 REDIS_TEST_URL={test_redis_url!r}"
    )

os.environ["REDIS_URL"] = test_redis_url

if not os.environ["POSTGRES_DB"].endswith("_test"):
    raise RuntimeError(
        f"测试库名必须以 _test 结尾，当前是 {os.environ['POSTGRES_DB']}"
    )


import pytest  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.db.redis import close_redis, create_redis_client  # noqa: E402
from app.db.session import AsyncSessionFactory, engine, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.tests.fakes import FakeLLMProvider  # noqa: E402

# 这是一个固定的有效 bcrypt 哈希，只用于构造不关心密码流程的测试用户。
# 注册/登录测试仍会调用真实的 hash_password / verify_password。
TEST_PASSWORD_HASH = "$2b$12$D6pRJxwBIEpO4eR/l/7OVOmJotevDNKnZxC2.0ebLckx9bpookcpu"


def _upgrade_head() -> None:
    # 绝对路径，不依赖“当前目录刚好是项目根目录”
    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini_path))
    command.upgrade(cfg, "head")


async def _recreate_schema_and_upgrade() -> None:
    async with engine.begin() as conn:
        # 测试库已在模块加载时 fail-closed 校验；这里才允许重建 schema。
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await asyncio.to_thread(_upgrade_head)


@pytest.fixture(scope="session")
async def real_redis():
    """真实且已校验为独立测试 DB 的 Redis 客户端。"""
    client = create_redis_client(os.environ["REDIS_URL"])
    try:
        await client.ping()
    except Exception as exc:
        await close_redis(client)
        raise RuntimeError("测试 Redis 不可用，拒绝运行测试") from exc

    yield client
    await close_redis(client)


@pytest.fixture(scope="session")
async def migrated_schema():
    """整个测试会话只从空库跑一次迁移，确保日常用例使用真实迁移后的结构。"""
    await _recreate_schema_and_upgrade()
    yield


@pytest.fixture
async def fresh_schema(migrated_schema, real_redis):
    """每个用例清空业务数据和独立测试 Redis。"""
    await real_redis.flushdb()
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE TABLE messages, conversations, users RESTART IDENTITY CASCADE")
        )
    yield
    await real_redis.flushdb()


@pytest.fixture(scope="module")
async def upgraded_empty_schema():
    """迁移测试专用：从空 schema 升级到 head，一次即可。"""
    await _recreate_schema_and_upgrade()
    yield


@pytest.fixture
def create_test_user(fresh_schema):
    """创建已认证场景需要的用户，不把注册/登录成本带入无关测试。"""
    async def _create(username: str, role: str = "user") -> User:
        async with AsyncSessionFactory() as session:
            user = User(
                username=username,
                password_hash=TEST_PASSWORD_HASH,
                role=role,
            )
            session.add(user)
            await session.commit()
            return user

    return _create


@pytest.fixture
def redis_client() -> AsyncMock:
    """非缓存测试用的 Redis 替身；缓存集成测试必须另用真实 Redis。"""
    client = AsyncMock()
    client.get.return_value = None
    return client


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    """测试必须显式注入 provider，绝不自动连接真实模型。"""
    return FakeLLMProvider()


@pytest.fixture
async def redis_test_client(fresh_schema, real_redis):
    """缓存集成测试使用的真实 Redis；fresh_schema 已完成清理。"""
    return real_redis


@pytest.fixture
async def client(fresh_schema, redis_client, llm_provider):
    async def override_get_db():
        async with AsyncSessionFactory() as session:
            yield session

    test_app = create_app(llm_provider=llm_provider)
    test_app.dependency_overrides[get_db] = override_get_db
    async with test_app.router.lifespan_context(test_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as test_client:
            yield test_client
    test_app.dependency_overrides.clear()


@pytest.fixture
async def test_user_id(create_test_user) -> int:
    """为 Repository/Service 测试创建明确的资源所有者。"""
    user = await create_test_user("test-owner")
    return user.id


@pytest.fixture
async def auth_headers(client, create_test_user) -> dict[str, str]:
    """为受保护接口测试提供有效 JWT，认证接口另有专门的真实 bcrypt 用例。"""
    user = await create_test_user("api-user")
    token = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}
