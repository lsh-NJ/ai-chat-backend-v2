from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MessageRetryJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    job_id: UUID
    idempotency_key: UUID
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    content: str
    is_complete: bool
    attempt: int = Field(default=0, ge=0)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value
