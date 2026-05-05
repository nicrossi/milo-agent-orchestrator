from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime, timezone
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
    deadline: datetime = Field(
        ...,
        description="Date and time by which students must complete the activity. Must be in the future.",
    )
    course_ids: Optional[List[UUID]] = Field(
        default=None,
        description="Optional list of course IDs to assign this activity to immediately.",
    )

    @field_validator("deadline")
    @classmethod
    def _deadline_must_be_future(cls, value: datetime) -> datetime:
        # Normalise to timezone-aware UTC so comparison is well-defined.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if value <= datetime.now(timezone.utc):
            raise ValueError("deadline must be in the future")
        return value

class ActivityUpdate(BaseModel):
    title: Optional[str] = None
    teacher_goal: Optional[str] = None
    context_description: Optional[str] = None
    status: Optional[ActivityStatus] = None
    deadline: Optional[datetime] = None


class ActivityAssignCoursesRequest(BaseModel):
    course_ids: List[UUID] = Field(
        ...,
        min_length=1,
        description="One or more course IDs that should receive this activity.",
    )

# ------------------------------------------------------------------
# RESPONSE PAYLOADS (What the backend sends to the frontend)
# ------------------------------------------------------------------
class CourseRef(BaseModel):
    id: UUID
    name: str

    class Config:
        from_attributes = True


class StudentSessionRef(BaseModel):
    """The requesting student's most recent session for an activity, used by
    the activity card to render Start / Resume / Finished states."""
    id: UUID
    status: SessionStatus
    started_at: datetime
    finalized_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ActivityStudentResponse(BaseModel):
    id: UUID
    title: str
    context_description: str
    status: ActivityStatus
    created_by_id: str
    deadline: Optional[datetime] = None
    courses: List[CourseRef] = Field(default_factory=list)
    # Populated only on the student-facing list endpoint, scoped to the
    # requesting user. Null when no session exists yet.
    student_session: Optional[StudentSessionRef] = None

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
    # Set when the LLM judges the reflection has reached natural closure.
    # The teacher analytics view treats this as the source of truth for
    # "finished" — sessions with finalized_at IS NULL are rendered as
    # Pending regardless of metrics-pipeline status.
    finalized_at: Optional[datetime] = None

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
