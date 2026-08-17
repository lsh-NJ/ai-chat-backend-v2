from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=8, max_length=32)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"