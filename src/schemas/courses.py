from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, Field


class CourseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class AddStudentRequest(BaseModel):
    student_id: str = Field(..., min_length=1, max_length=255)


class CourseResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    created_by_id: str
    created_at: datetime

    class Config:
        from_attributes = True


class CourseStudentResponse(BaseModel):
    student_id: str
    display_name: str
    email: str


class CourseDetailResponse(BaseModel):
    course: CourseResponse
    students: List[CourseStudentResponse]

