from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from uuid import UUID


class MessageDTO(BaseModel):
    role: str
    content: str


class UIMessageDTO(MessageDTO):
    id: UUID
    session_id: Optional[UUID] = None
    created_at: Optional[datetime] = None


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, description="The user's input question")


class ChatResponse(BaseModel):
    answer: str