from pydantic import BaseModel

from app.schemas.chat import NonEmptyMessage


class StructuredExtractRequest(BaseModel):
    text: NonEmptyMessage


class StructuredExtractResponse(BaseModel):
    conversation_id: int
    topic: str
    sentiment: str
