"""把本地 Markdown / HTML 文档导入 PostgreSQL + pgvector（模拟上传入口）。

当前项目还没有 HTTP 文件上传 API；Week 16 先用这个 CLI 模拟“用户上传资料”。
Week 17 会把它改造成异步 ingestion API。

用法：

    HF_HOME=/tmp/hf-cache .venv/bin/python -m experiments.import_documents \
      --path docs/ \
      --tenant-id user:1

注意：
- tenant_id 必须与认证用户的 __tenant 映射一致：当前是 user:{user_id}；
- 支持 .md/.markdown/.html/.htm；
- 使用 BAAI/bge-small-zh-v1.5 生成 512 维向量。
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

from app.db.session import AsyncSessionFactory  # noqa: E402
from app.rag.chunking import chunk_document  # noqa: E402
from app.rag.embedding import SentenceTransformerEmbedder  # noqa: E402
from app.rag.ingestion import IngestionStatus, ingest_document_async  # noqa: E402
from app.rag.parsers import DocumentFormat, parse_document  # noqa: E402
from app.rag.postgres_store import (  # noqa: E402
    PostgresChunkStore,
    PostgresDocumentStore,
)

SUPPORTED_SUFFIXES: dict[str, DocumentFormat] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--overlap", type=int, default=50)
    parser.add_argument(
        "--embedding-model",
        default=os.environ["EMBEDDING_MODEL"],
    )
    return parser.parse_args()


def _iter_files(path: Path) -> tuple[Path, ...]:
    if path.is_file():
        files = (path,)
    elif path.is_dir():
        files = tuple(
            child
            for child in sorted(path.rglob("*"))
            if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES
        )
    else:
        raise ValueError(f"path does not exist: {path}")
    if not files:
        raise ValueError(f"no supported files found under: {path}")
    return files


async def _import_file(
    *,
    path: Path,
    tenant_id: str,
    chunk_size: int,
    overlap: int,
    embedder: SentenceTransformerEmbedder,
) -> None:
    format_name = SUPPORTED_SUFFIXES[path.suffix.lower()]
    raw_text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    document = parse_document(
        raw_text,
        source=str(path),
        format_name=format_name,
        metadata={"filename": path.name},
    )

    async with AsyncSessionFactory() as session:
        result = await ingest_document_async(
            PostgresDocumentStore(session, tenant_id=tenant_id),
            document,
        )
        await session.commit()
        persisted_document = result.document

    chunks = chunk_document(
        persisted_document,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    async with AsyncSessionFactory() as session:
        await PostgresChunkStore(
            session,
            tenant_id=tenant_id,
        ).save_chunks(
            persisted_document.id,
            chunks,
            embedder=embedder.embed_query,
        )
        await session.commit()

    status = (
        result.status.value
        if isinstance(result.status, IngestionStatus)
        else str(result.status)
    )
    print(
        f"{status:<8} document_id={persisted_document.id} "
        f"chunks={len(chunks)} file={path}"
    )


async def main() -> None:
    args = _parse_args()
    files = _iter_files(args.path)
    embedder = SentenceTransformerEmbedder(args.embedding_model)
    print(
        f"tenant_id={args.tenant_id} files={len(files)} "
        f"embedding_model={args.embedding_model}"
    )
    for path in files:
        await _import_file(
            path=path,
            tenant_id=args.tenant_id,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            embedder=embedder,
        )


if __name__ == "__main__":
    asyncio.run(main())
