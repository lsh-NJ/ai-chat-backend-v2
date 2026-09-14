"""RAG 检索底座的 PostgreSQL 表结构（Week 15 Day 1）。

设计要点：
- `tenant_id` 是租户隔离的第一公民，参与复合主键；
- `document_id` / `chunk_id` 沿用 Week13/14 的业务字符串；
- `rag_chunks` 通过 `(tenant_id, document_id)` 复合外键保证
  chunk 与 document 必然属于同一租户；
- `embedding` 使用 pgvector 的 `vector(128)`，Day 3 写入向量；
- metadata 用 JSONB，保留 Week13/14 的灵活键值语义。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

RAG_EMBEDDING_DIMENSION = 512


class RagDocument(Base):
    """一份已入库文档（含版本化 id 与租户边界）。"""

    __tablename__ = "rag_documents"

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "document_id",
            name="pk_rag_documents",
        ),
        UniqueConstraint(
            "tenant_id",
            "content_hash",
            name="uq_rag_documents_tenant_content_hash",
        ),
        UniqueConstraint(
            "tenant_id",
            "source",
            "version",
            name="uq_rag_documents_tenant_source_version",
        ),
        Index(
            "ix_rag_documents_tenant_source",
            "tenant_id",
            "source",
        ),
        CheckConstraint(
            "length(tenant_id) > 0 AND length(document_id) > 0",
            name="ck_rag_documents_tenant_document_nonempty",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_rag_documents_version_positive",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source: Mapped[str] = mapped_column(String(1024), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    chunks: Mapped[list[RagChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RagChunk(Base):
    """一个可检索的文本片段，可追踪回 rag_documents。"""

    __tablename__ = "rag_chunks"

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "chunk_id",
            name="pk_rag_chunks",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["rag_documents.tenant_id", "rag_documents.document_id"],
            name="fk_rag_chunks_tenant_document_rag_documents",
            ondelete="CASCADE",
        ),
        Index(
            "ix_rag_chunks_tenant_document",
            "tenant_id",
            "document_id",
        ),
        Index(
            "ix_rag_chunks_content_fts",
            text("to_tsvector('simple', content)"),
            postgresql_using="gin",
        ),
        Index(
            "ix_rag_chunks_content_cjk_fts",
            text(
                "to_tsvector('simple'::regconfig, "
                "regexp_replace(content, '([^[:ascii:]])', ' \\1 ', 'g'))"
            ),
            postgresql_using="gin",
        ),
        CheckConstraint(
            "length(tenant_id) > 0 AND length(chunk_id) > 0",
            name="ck_rag_chunks_tenant_chunk_nonempty",
        ),
        CheckConstraint(
            # end 是 SQL 保留字，裸写在 CHECK 表达式里会导致 DDL 语法错误；
            # ORM 生成的普通 SQL 会自动加引号，这里必须显式写成 "end"。
            'start >= 0 AND "end" > start',
            name="ck_rag_chunks_start_end",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(1024), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    start: Mapped[int] = mapped_column(Integer, nullable=False)
    end: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(RAG_EMBEDDING_DIMENSION),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    document: Mapped[RagDocument] = relationship(
        back_populates="chunks",
    )
