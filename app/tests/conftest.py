import os
from dotenv import load_dotenv

load_dotenv()

os.environ["POSTGRES_DB"] = os.environ["POSTGRES_TEST_DB"]

if not os.environ["POSTGRES_DB"].endswith("_test"):
    raise RuntimeError(
        f"测试库名必须以 _test 结尾，当前是 {os.environ['POSTGRES_DB']}"
    )

import pytest  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import AsyncSessionFactory, engine  # noqa: E402


@pytest.fixture
async def fresh_schema():
    # 每个测试重建一次表结构，测试之间互不影响
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield