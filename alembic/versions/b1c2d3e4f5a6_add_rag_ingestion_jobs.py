"""添加 rag_ingestion_jobs 任务表。

Week 17 Day 1：上传接口只创建 pending 任务，worker 后续异步推进状态。
本表是“HTTP 请求”和“后台处理”之间的持久化桥梁。

Revision ID: b1c2d3e4f5a6
Revises: a2b3c4d5e6f7
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_ingestion_jobs",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column(
            "content_type",
            sa.String(length=255),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(tenant_id) > 0 AND length(job_id) > 0",
            name=op.f("ck_rag_ingestion_jobs_tenant_job_nonempty"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name=op.f("ck_rag_ingestion_jobs_status"),
        ),
        sa.CheckConstraint(
            "size_bytes >= 0",
            name=op.f("ck_rag_ingestion_jobs_size_nonnegative"),
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name=op.f("ck_rag_ingestion_jobs_attempts"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "job_id",
            name=op.f("pk_rag_ingestion_jobs"),
        ),
    )
    op.create_index(
        op.f("ix_rag_ingestion_jobs_tenant_status"),
        "rag_ingestion_jobs",
        ["tenant_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_rag_ingestion_jobs_tenant_status"),
        table_name="rag_ingestion_jobs",
    )
    op.drop_table("rag_ingestion_jobs")
