"""disable migration backfill user

Revision ID: c7d8e9f0a1b2
Revises: b18f6a4d2c90
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b18f6a4d2c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE users SET is_active = false WHERE username = 'default'"
        )
    )


def downgrade() -> None:
    op.drop_column("users", "is_active")
