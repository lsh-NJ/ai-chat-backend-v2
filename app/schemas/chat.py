from typing import Annotated
from pydantic import BaseModel, StringConstraints

NonEmptyMessage = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]

class ChatRequest(BaseModel):
    conversation_id: int | None = None
    message: NonEmptyMessage

class ChatResponse(BaseModel):
    conversation_id: int
    reply: str

