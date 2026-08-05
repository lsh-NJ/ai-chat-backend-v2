import os
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

from app.db.base import Base  # noqa: E402
from app.db.session import AsyncSessionFactory, engine  # noqa: E402


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