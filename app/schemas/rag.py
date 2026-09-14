"""RAG API 的请求/响应模型（Week 16 Day 1-3）。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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
