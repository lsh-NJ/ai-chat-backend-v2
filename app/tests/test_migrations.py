from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.db.session import engine

def _alembic_config() -> Config:
    # app/tests/test_migrations.py 的 parents[2] 是项目根目录
    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    return Config(str(ini_path))


def _head_revision() -> str:
    scripts = ScriptDirectory.from_config(_alembic_config())
    return scripts.get_current_head()


# 目标：空库 upgrade head 后，业务表全部存在
async def test_empty_schema_upgrade_creates_all_tables(fresh_schema):
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public'"
            )
        )
        tables = {row[0] for row in rows}

    assert {"conversations", "messages", "alembic_version"} <= tables


# 目标：迁移完成后 alembic_version 与代码最新版本一致
async def test_alembic_version_is_at_head(fresh_schema):
    async with engine.connect() as conn:
        version = (
            await conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
        ).scalar_one()

    assert version == _head_revision()
