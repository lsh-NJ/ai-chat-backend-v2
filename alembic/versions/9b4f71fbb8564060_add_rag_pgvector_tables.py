"""add rag_documents and rag_chunks with pgvector

Revision ID: 9b4f71fbb8564060
Revises: d8e9f0a1b2c3
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b4f71fbb8564060"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create pgvector extension and the RAG storage tables."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "rag_documents",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=1024), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(tenant_id) > 0 AND length(document_id) > 0",
            name="ck_rag_documents_tenant_document_nonempty",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "document_id",
            name=op.f("pk_rag_documents"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "content_hash",
            name=op.f("uq_rag_documents_tenant_content_hash"),
        ),
    )
    op.create_index(
        op.f("ix_rag_documents_tenant_source"),
        "rag_documents",
        ["tenant_id", "source"],
        unique=False,
    )

    op.create_table(
        "rag_chunks",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_id", sa.String(length=255), nullable=False),
        sa.Column("document_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=1024), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=128),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(tenant_id) > 0 AND length(chunk_id) > 0",
            name="ck_rag_chunks_tenant_chunk_nonempty",
        ),
        sa.CheckConstraint(
            # end 是 SQL 保留字，CHECK 表达式不会自动引用标识符，必须手写双引号。
            'start >= 0 AND "end" > start',
            name="ck_rag_chunks_start_end",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["rag_documents.tenant_id", "rag_documents.document_id"],
            name=op.f("fk_rag_chunks_tenant_document_rag_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "chunk_id",
            name=op.f("pk_rag_chunks"),
        ),
    )
    op.create_index(
        op.f("ix_rag_chunks_tenant_document"),
        "rag_chunks",
        ["tenant_id", "document_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop RAG storage tables; keep the shared vector extension."""
    op.drop_index(
        op.f("ix_rag_chunks_tenant_document"),
        table_name="rag_chunks",
    )
    op.drop_table("rag_chunks")
    op.drop_index(
        op.f("ix_rag_documents_tenant_source"),
        table_name="rag_documents",
    )
    op.drop_table("rag_documents")
