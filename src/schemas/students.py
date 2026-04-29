from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from src.core.models import SessionStatus
from src.schemas.activities import (
    CalibrationMetricResult,
    CourseRef,
    ReflectionMetricResult,
    TransferMetricResult,
)


class TeacherStudentResponse(BaseModel):
    """A student enrolled in any course owned by the requesting teacher."""
    student_id: str
    display_name: str
    email: str
    courses: List[CourseRef] = Field(default_factory=list)
    session_count: int = 0


class StudentSessionDetail(BaseModel):
    """A single session of a student in any of the teacher's activities."""
    session_id: UUID
    activity_id: UUID
    activity_title: str
    status: SessionStatus
    started_at: datetime
    reflection_quality: Optional[ReflectionMetricResult] = None
    calibration: Optional[CalibrationMetricResult] = None
    contextual_transfer: Optional[TransferMetricResult] = None
