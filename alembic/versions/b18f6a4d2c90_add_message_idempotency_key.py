"""添加用于消息保存队列的 messages.idempotency_key

Revision ID: b18f6a4d2c90
Revises: 4fd2b8cde52e
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b18f6a4d2c90"
down_revision: Union[str, Sequence[str], None] = "4fd2b8cde52e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add a nullable business idempotency key for retryable messages."""
    op.add_column(
        "messages",
        sa.Column("idempotency_key", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_messages_idempotency_key",
        "messages",
        ["idempotency_key"],
    )


def downgrade() -> None:
    """Remove the idempotency key and its unique constraint."""
    op.drop_constraint(
        "uq_messages_idempotency_key",
        "messages",
        type_="unique",
    )
    op.drop_column("messages", "idempotency_key")
