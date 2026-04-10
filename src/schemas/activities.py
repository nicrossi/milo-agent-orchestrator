from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from uuid import UUID

class ActivityStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"

class SessionStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_EVALUATION = "PENDING_EVALUATION"
    EVALUATED = "EVALUATED"
    EVALUATION_FAILED = "EVALUATION_FAILED"

class MetricLevel(str, Enum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"

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

class MetricResult(BaseModel):
    level: MetricLevel
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
    reflection_quality: Optional[MetricResult] = None
    calibration: Optional[MetricResult] = None
    contextual_transfer: Optional[MetricResult] = None

    class Config:
        from_attributes = True

class ActivityDashboardResponse(BaseModel):
    """
    The master payload sent to the Teacher Dashboard when they click on a specific activity.
    """
    activity: ActivityTeacherResponse
    results: List[StudentSessionResult]
