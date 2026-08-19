from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str | None = None
    created_at: datetime

class ConversationCreateRequest(BaseModel):
    title: str | None = None