"""上传文件的存储抽象（Week 17 Day 1）。

API 进程和 ingestion worker 可能不是同一个进程，因此上传的文件不能只留在
内存里，必须落到一个双方都能访问的位置。当前提供本地文件系统实现；
接口按对象存储语义设计，后续可以替换为 S3 / MinIO 而不改业务代码。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Protocol, runtime_checkable


class UploadStorageError(ValueError):
    """上传文件存储失败时抛出的异常。"""


@runtime_checkable
class FileStorage(Protocol):
    """按 key 读写二进制内容的最小存储契约。"""

    async def save(self, key: str, data: bytes) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...


class LocalFileStorage:
    """把文件保存在本地目录下；key 只允许是单段相对路径。

    生产环境应替换为对象存储，并加上服务端加密、生命周期策略和病毒扫描。
    """

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a pathlib.Path")
        self._root = root

    @classmethod
    def from_env(cls) -> LocalFileStorage:
        """从 `RAG_UPLOAD_DIR` 构造本地存储，缺省使用 data/rag_uploads。"""
        raw_root = os.environ.get("RAG_UPLOAD_DIR", "data/rag_uploads")
        return cls(Path(raw_root))

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path:
        if not isinstance(key, str) or not key.strip():
            raise UploadStorageError("storage key must be a non-empty string")
        # 只接受单段 key，阻止 ../ 路径穿越。
        if key != Path(key).name:
            raise UploadStorageError("storage key must not contain path separators")
        return self._root / key

    async def save(self, key: str, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        path = self._path_for(key)
        try:
            await asyncio.to_thread(self._save_sync, path, data)
        except OSError as exc:
            raise UploadStorageError(f"failed to save upload file: {exc}") from exc

    @staticmethod
    def _save_sync(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再原子替换，避免 worker 读到半个文件。
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_bytes(data)
        os.replace(temp_path, path)

    async def read(self, key: str) -> bytes:
        path = self._path_for(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise UploadStorageError(f"failed to read upload file: {exc}") from exc

    async def delete(self, key: str) -> None:
        path = self._path_for(key)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError as exc:
            raise UploadStorageError(f"failed to delete upload file: {exc}") from exc
