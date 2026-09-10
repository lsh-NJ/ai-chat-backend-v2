"""add full-text search index for rag_chunks

Revision ID: c4d5e6f7a8b9
Revises: b8bb129aaf3f43d2
Create Date: 2026-09-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b8bb129aaf3f43d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为 chunk 正文建立 PostgreSQL 全文检索表达式索引。"""
    op.create_index(
        "ix_rag_chunks_content_fts",
        "rag_chunks",
        [sa.text("to_tsvector('simple', content)")],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    """删除全文检索索引。"""
    op.drop_index(
        "ix_rag_chunks_content_fts",
        table_name="rag_chunks",
    )
