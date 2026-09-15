"""RAG API 的请求/响应模型（Week 16 Day 1-3，Week 17 Day 1 扩展上传任务）。"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.rag.ingestion_job import IngestionJobStatus

NonEmptyQuestion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]


class RagQueryRequest(BaseModel):
    """客户端只能提交问题与非权限参数。"""

    model_config = ConfigDict(extra="forbid")

    question: NonEmptyQuestion
    top_k: int = Field(default=5, ge=1, le=50)


class RagCitationOut(BaseModel):
    label: str
    chunk_id: str
    document_id: str
    source: str
    start: int
    end: int
    score: float


class RagAnswerResponse(BaseModel):
    answer: str
    citations: list[RagCitationOut]
    retrieved_chunk_ids: list[str]
    refused: bool
    refusal_reason: str | None = None


class RagDocumentUploadResponse(BaseModel):
    """上传接口只返回任务身份，不等待 ingestion 完成。"""

    job_id: str
    status: IngestionJobStatus
    filename: str
    size_bytes: int
    content_sha256: str
    created_at: datetime


class RagIngestionJobResponse(BaseModel):
    """供前端轮询的 ingestion 任务状态。"""

    job_id: str
    filename: str
    content_type: str
    size_bytes: int
    status: IngestionJobStatus
    attempts: int
    max_attempts: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
