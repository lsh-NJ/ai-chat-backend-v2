"""repair legacy message completeness column drift

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("messages")
    }

    if {"is_complete", "is_completed"} <= columns:
        raise RuntimeError(
            "messages contains both is_complete and is_completed; "
            "refusing an ambiguous automatic repair"
        )
    if "is_completed" in columns:
        op.alter_column(
            "messages",
            "is_completed",
            new_column_name="is_complete",
            existing_type=sa.Boolean(),
            existing_nullable=False,
        )
        return
    if "is_complete" not in columns:
        raise RuntimeError(
            "messages has no completeness column; refusing an unsafe repair"
        )


def downgrade() -> None:
    # 此 revision 修复的是 c7d8e9f0a1b2 之前就应成立的结构不变量。
    # 降级到 c7 后仍应保留规范列名 is_complete，因此无需反向改名。
    pass
