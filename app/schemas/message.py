from datetime import datetime
from pydantic import BaseModel, ConfigDict

class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    created_at: datetime

