"""将 rag_chunks.embedding 从 128 维升级为真实中文 embedding 的 512 维。

Week 15 的 ``hash_embed`` 只是 128 维玩具向量；Week 16 接入
``BAAI/bge-small-zh-v1.5`` 后输出 512 维。旧向量没有保留价值，
因此 downgrade/upgrade 都直接重建列，避免 pgvector 对维度变更的限制。

Revision ID: f1a2b3c4d5e6
Revises: c4d5e6f7a8b9
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("rag_chunks", "embedding")
    op.add_column(
        "rag_chunks",
        sa.Column("embedding", Vector(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rag_chunks", "embedding")
    op.add_column(
        "rag_chunks",
        sa.Column("embedding", Vector(128), nullable=True),
    )
