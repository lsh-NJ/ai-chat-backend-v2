"""RAG 切分：把 Document 切成可检索、可追踪来源的 Chunk。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from app.rag.documents import Document


@dataclass(frozen=True, slots=True)
class Chunk:
    """一个可检索、可追踪回原文的文本片段。"""

    id: str
    document_id: str
    source: str
    content: str
    metadata: Mapping[str, Any]
    start: int
    end: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("chunk id must be a non-empty string")
        if not isinstance(self.document_id, str) or not self.document_id.strip():
            raise ValueError("chunk document_id must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("chunk source must be a non-empty string")
        if not isinstance(self.content, str) or not self.content:
            raise ValueError("chunk content must be a non-empty string")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("chunk metadata must be a mapping")
        if not isinstance(self.start, int) or not isinstance(self.end, int):
            raise TypeError("chunk start/end must be integers")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("chunk start/end must satisfy 0 <= start < end")
        if len(self.content) != self.end - self.start:
            raise ValueError("chunk content length must equal end - start")

        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


def _validate_params(chunk_size: int, overlap: int) -> None:
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
        raise TypeError("chunk_size must be an integer")
    if not isinstance(overlap, int) or isinstance(overlap, bool):
        raise TypeError("overlap must be an integer")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")


def chunk_document(
    document: Document,
    *,
    chunk_size: int,
    overlap: int = 0,
) -> list[Chunk]:
    """把 Document 按固定长度 + 重叠切成 Chunk。

    - start/end 是 chunk 在 document.content 中的字符位置；
    - 每个 chunk 继承文档元数据，并附加 chunk_index / chunk_count；
    - 纯空白窗口不产出 chunk。
    """
    if not isinstance(document, Document):
        raise TypeError("document must be a Document")
    _validate_params(chunk_size, overlap)

    content = document.content
    if not content:
        raise ValueError("cannot chunk an empty document")

    step = chunk_size - overlap
    raw_windows: list[tuple[int, int]] = []

    start = 0
    while start < len(content):
        end = min(start + chunk_size, len(content))
        if content[start:end].strip():
            raw_windows.append((start, end))
        if end == len(content):
            break
        start += step

    chunk_count = len(raw_windows)
    chunks: list[Chunk] = []

    for index, (start, end) in enumerate(raw_windows):
        metadata = dict(document.metadata)
        metadata["chunk_index"] = index
        metadata["chunk_count"] = chunk_count
        chunk_id = f"{document.id}::chunk-{start}-{end}"
        chunks.append(
            Chunk(
                id=chunk_id,
                document_id=document.id,
                source=document.source,
                content=content[start:end],
                metadata=metadata,
                start=start,
                end=end,
            )
        )

    return chunks
