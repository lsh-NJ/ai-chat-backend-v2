import asyncio
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from alembic import command
from app.db.session import engine


def _alembic_config() -> Config:
    # app/tests/test_database/test_migrations.py 的 parents[3] 是项目根目录
    ini_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    return Config(str(ini_path))


def _head_revision() -> str | None:
    scripts = ScriptDirectory.from_config(_alembic_config())
    return scripts.get_current_head()


# 目标：空库 upgrade head 后，业务表全部存在
async def test_empty_schema_upgrade_creates_all_tables(upgraded_empty_schema):
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
async def test_alembic_version_is_at_head(upgraded_empty_schema):
    async with engine.connect() as conn:
        version = (
            await conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
        ).scalar_one()

    assert version == _head_revision()


async def test_migration_backfill_user_is_inactive(upgraded_empty_schema):
    async with engine.connect() as conn:
        is_active = (
            await conn.execute(
                text("SELECT is_active FROM users WHERE username = 'default'")
            )
        ).scalar_one()

    assert is_active is False


# 目标：ORM 模型与迁移 head 一致，不存在遗漏的新迁移
async def test_models_match_migration_head(upgraded_empty_schema):
    await asyncio.to_thread(command.check, _alembic_config())


async def test_repair_migration_renames_legacy_completeness_column(
    upgraded_empty_schema,
):
    config = _alembic_config()
    await asyncio.to_thread(command.downgrade, config, "c7d8e9f0a1b2")

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "ALTER TABLE messages "
                "RENAME COLUMN is_complete TO is_completed"
            )
        )

    await asyncio.to_thread(command.upgrade, config, "head")

    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'messages'"
            )
        )
        columns = {row[0] for row in rows}

    assert "is_complete" in columns
    assert "is_completed" not in columns
