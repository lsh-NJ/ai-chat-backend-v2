"""RAG 文档模型与内容去重契约。"""

from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping


def compute_content_hash(content: str) -> str:
    """计算文档内容的稳定 SHA-256 哈希，用于去重。"""

    if not isinstance(content, str):
        raise TypeError("content must be a string")
    return sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Document:
    """一份已解析、可去重、可追踪来源的文档。"""

    id: str
    source: str
    content: str
    metadata: Mapping[str, Any] = MappingProxyType({})
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("document id must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("document source must be a non-empty string")
        if not isinstance(self.content, str):
            raise TypeError("document content must be a string")
        if not self.content.strip():
            raise ValueError("document content must not be empty")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("document metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "content_hash", compute_content_hash(self.content))
