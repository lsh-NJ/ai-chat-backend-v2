"""添加中文字符级 FTS 表达式索引。

PostgreSQL `simple` parser 不会切分中文，因此 Week 16 在 SQL 层把非 ASCII
字符逐个隔开，再建立 GIN 表达式索引。该方案不依赖 zhparser/pg_jieba，
用于 Week 16 baseline；生产环境仍建议替换为专业中文分词扩展。

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CJK_FTS_EXPRESSION = (
    "to_tsvector('simple'::regconfig, "
    "regexp_replace(content, '([^[:ascii:]])', ' \\1 ', 'g'))"
)


def upgrade() -> None:
    op.create_index(
        "ix_rag_chunks_content_cjk_fts",
        "rag_chunks",
        [sa.text(_CJK_FTS_EXPRESSION)],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rag_chunks_content_cjk_fts",
        table_name="rag_chunks",
    )
