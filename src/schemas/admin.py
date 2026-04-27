from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AdminUserCreate(BaseModel):
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    display_name: str = Field(..., min_length=1, max_length=255)
    password: Optional[str] = Field(None, min_length=6, max_length=128)


class AdminUserResponse(BaseModel):
    uid: str
    email: str
    display_name: str
    password: Optional[str] = None  # only populated on creation


class AdminCourseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    teacher_id: str = Field(..., min_length=1, max_length=255)


class AdminEnrollRequest(BaseModel):
    student_id: str = Field(..., min_length=1, max_length=255)


class AdminTransferTeacherRequest(BaseModel):
    teacher_id: str = Field(..., min_length=1, max_length=255)


class AdminCourseResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    teacher_id: str
    teacher_name: Optional[str]
    teacher_email: Optional[str]
    student_ids: List[str]
    created_at: datetime
