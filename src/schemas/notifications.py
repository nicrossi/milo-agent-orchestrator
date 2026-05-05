from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: UUID
    type: str
    activity_id: Optional[UUID] = None
    title: str
    body: Optional[str] = None
    deep_link: str
    read_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True
