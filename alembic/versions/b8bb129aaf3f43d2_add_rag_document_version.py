"""add document version to rag_documents

Revision ID: b8bb129aaf3f43d2
Revises: 9b4f71fbb8564060
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8bb129aaf3f43d2"
down_revision: Union[str, Sequence[str], None] = "9b4f71fbb8564060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add an explicit version column and its per-source uniqueness."""
    op.add_column(
        "rag_documents",
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_rag_documents_version_positive",
        "rag_documents",
        "version > 0",
    )
    op.create_unique_constraint(
        "uq_rag_documents_tenant_source_version",
        "rag_documents",
        ["tenant_id", "source", "version"],
    )


def downgrade() -> None:
    """Remove the version uniqueness and column."""
    op.drop_constraint(
        "uq_rag_documents_tenant_source_version",
        "rag_documents",
        type_="unique",
    )
    op.drop_constraint(
        "ck_rag_documents_version_positive",
        "rag_documents",
        type_="check",
    )
    op.drop_column("rag_documents", "version")
