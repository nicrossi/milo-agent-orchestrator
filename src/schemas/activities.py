from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from uuid import UUID

from src.core.models import (
    ActivityStatus,
    SessionStatus,
    ReflectionLevel,
    CalibrationLevel,
    TransferLevel,
)

class ResultsSortBy(str, Enum):
    STARTED_AT = "started_at"
    # Future: STUDENT_NAME = "student_name"

class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"

# ------------------------------------------------------------------
# REQUEST PAYLOADS (What the frontend sends to the backend)
# ------------------------------------------------------------------
class ActivityCreate(BaseModel):
    title: str = Field(..., max_length=255, description="Visible to students")
    teacher_goal: str = Field(..., description="Hidden from students, used by AI")
    context_description: str = Field(..., description="The prompt for the student and AI")
    status: ActivityStatus = Field(default=ActivityStatus.PUBLISHED)

class ActivityUpdate(BaseModel):
    title: Optional[str] = None
    teacher_goal: Optional[str] = None
    context_description: Optional[str] = None
    status: Optional[ActivityStatus] = None

# ------------------------------------------------------------------
# RESPONSE PAYLOADS (What the backend sends to the frontend)
# ------------------------------------------------------------------
class ActivityStudentResponse(BaseModel):
    id: UUID
    title: str
    context_description: str
    status: ActivityStatus
    created_by_id: str
    
    class Config:
        from_attributes = True

# For Teachers - Inherits and adds the secret fields
class ActivityTeacherResponse(ActivityStudentResponse):
    teacher_goal: str

class ReflectionMetricResult(BaseModel):
    level: ReflectionLevel
    justification: Optional[str] = None
    evidence: Optional[List[str]] = None
    recommended_action: Optional[str] = None

class CalibrationMetricResult(BaseModel):
    level: CalibrationLevel
    justification: Optional[str] = None
    evidence: Optional[List[str]] = None
    recommended_action: Optional[str] = None

class TransferMetricResult(BaseModel):
    level: TransferLevel
    justification: Optional[str] = None
    evidence: Optional[List[str]] = None
    recommended_action: Optional[str] = None

class StudentSessionResult(BaseModel):
    """
    Represents a single student's attempt at an activity.
    Combines data from the Users, ChatSessions, and SessionMetrics tables.
    """
    session_id: UUID
    student_id: str
    student_name: str  # Joined from users.display_name
    status: SessionStatus
    started_at: datetime

    # AI Metrics (None when the session is still IN_PROGRESS or evaluation failed)
    reflection_quality: Optional[ReflectionMetricResult] = None
    calibration: Optional[CalibrationMetricResult] = None
    contextual_transfer: Optional[TransferMetricResult] = None

    class Config:
        from_attributes = True

class PaginatedStudentResults(BaseModel):
    """Paginated envelope for student session results."""
    items: List[StudentSessionResult]
    total: int
    page: int
    page_size: int
    total_pages: int

class ActivityDashboardResponse(BaseModel):
    """
    The master payload sent to the Teacher Dashboard when they click on a specific activity.
    """
    activity: ActivityTeacherResponse
    results: PaginatedStudentResults
