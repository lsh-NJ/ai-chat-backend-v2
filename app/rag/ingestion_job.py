"""文档 ingestion job 的领域契约与状态机（Week 17 Day 1）。

这个模块只表达“任务是什么状态、允许怎样迁移”，不依赖 FastAPI、ORM 或 Redis。
API 层、worker 层、测试层都依赖同一套状态定义，避免各自解释字符串。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class IngestionJobStatus(str, Enum):
    """一次文档 ingestion 的生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


#: 允许的状态迁移。
#:
#: - pending -> running：worker 领取任务；
#: - pending -> failed：入队后直接发现不可恢复错误；
#: - running -> succeeded / failed：执行结果；
#: - failed -> pending：人工或自动重试重新入队。
_ALLOWED_TRANSITIONS: dict[IngestionJobStatus, frozenset[IngestionJobStatus]] = {
    IngestionJobStatus.PENDING: frozenset(
        {IngestionJobStatus.RUNNING, IngestionJobStatus.FAILED}
    ),
    IngestionJobStatus.RUNNING: frozenset(
        {IngestionJobStatus.SUCCEEDED, IngestionJobStatus.FAILED}
    ),
    IngestionJobStatus.SUCCEEDED: frozenset(),
    IngestionJobStatus.FAILED: frozenset({IngestionJobStatus.PENDING}),
}


def can_transition(
    current: IngestionJobStatus,
    target: IngestionJobStatus,
) -> bool:
    """判断状态迁移是否合法。非法迁移必须 fail-closed，而不是静默接受。"""
    if not isinstance(current, IngestionJobStatus):
        raise TypeError("current must be an IngestionJobStatus")
    if not isinstance(target, IngestionJobStatus):
        raise TypeError("target must be an IngestionJobStatus")
    return target in _ALLOWED_TRANSITIONS[current]


@dataclass(frozen=True, slots=True)
class IngestionJob:
    """一份已上传、等待或已经处理的文档任务。

    该模型不承载文件正文；正文由对象存储 / 本地文件存储按 `storage_key` 读取。
    """

    tenant_id: str
    job_id: str
    filename: str
    content_type: str
    content_sha256: str
    storage_key: str
    size_bytes: int
    status: IngestionJobStatus = IngestionJobStatus.PENDING
    attempts: int = 0
    max_attempts: int = 3
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        if not isinstance(self.filename, str) or not self.filename.strip():
            raise ValueError("filename must be a non-empty string")
        if not isinstance(self.content_sha256, str) or len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a 64-character hex string")
        if not isinstance(self.storage_key, str) or not self.storage_key.strip():
            raise ValueError("storage_key must be a non-empty string")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool):
            raise TypeError("size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if not isinstance(self.status, IngestionJobStatus):
            raise TypeError("status must be an IngestionJobStatus")
        if not isinstance(self.attempts, int) or isinstance(self.attempts, bool):
            raise TypeError("attempts must be an integer")
        if self.attempts < 0:
            raise ValueError("attempts must be non-negative")
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool):
            raise TypeError("max_attempts must be an integer")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if self.attempts > self.max_attempts:
            raise ValueError("attempts must not exceed max_attempts")
