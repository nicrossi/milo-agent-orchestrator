import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from enum import Enum as PyEnum


class Base(DeclarativeBase):
    """
    Abstract base class for all SQLAlchemy declarative models.
    Provides the standard metadata and registry.
    """
    pass


class ChatMessage(Base):
    """Persistent storage for all chat messages across user sessions."""
    __tablename__ = "chat_messages"

    # Unique identifier for the exact message frame
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Groups messages belonging to the same conversation thread
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Firebase UID owner of the chat turn.
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Identifies the speaker. Must be either "user" or "model" to comply with Gemini API.
    role: Mapped[str] = mapped_column(String(20), nullable=False)

    # The actual text content.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Server-side timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_chat_messages_session_created", "session_id", "created_at"),
        Index("ix_chat_messages_user_session_created", "user_id", "session_id", "created_at"),
    )

    def __repr__(self) -> str:
        """
        Provides a fast, safe representation of the message for debugging logs.

        Deliberately excludes the actual `content` field to prevent flooding the console,
        shows the content length instead.
        """
        content_length = len(self.content) if self.content else 0
        return (
            f"<ChatMessage(id={self.id}, session={self.session_id}, user={self.user_id}, "
            f"role={self.role}, created_at={self.created_at}, "
            f"chars={content_length})>"
        )


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="student")
    photo_data_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class ActivityStatus(str, PyEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CourseEnrollment(Base):
    __tablename__ = "course_enrollments"
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    student_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    added_by_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ActivityCourseAssignment(Base):
    __tablename__ = "activity_course_assignments"
    activity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reflection_activities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReflectionActivity(Base):
    __tablename__ = "reflection_activities"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher_goal: Mapped[str] = mapped_column(Text, nullable=False)
    context_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ActivityStatus] = mapped_column(String(50), default=ActivityStatus.PUBLISHED)
    created_by_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)
    # Nullable for backwards compat with rows created before this column existed;
    # the API requires a deadline on creation going forward.
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deadline_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Idempotency marker for the teacher's deadline-summary notification
    # (sent once when the activity's deadline elapses, replacing the older
    # "all students completed" trigger).
    deadline_summary_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SessionStatus(str, PyEnum):
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_EVALUATION = "PENDING_EVALUATION"
    EVALUATED = "EVALUATED"
    EVALUATION_FAILED = "EVALUATION_FAILED"


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("reflection_activities.id"), nullable=False)
    student_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(String(50), default=SessionStatus.IN_PROGRESS)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    transcript: Mapped[str] = mapped_column(Text, server_default="", default="")
    # Phase 5: serialized PolicyStateSnapshot for resumable sessions.
    policy_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Set by the LLM when it judges the reflection has reached natural closure.
    # Status (PENDING_EVALUATION/EVALUATED) tracks the metrics-evaluation
    # pipeline; finalized_at separately tracks "the activity is truly done".
    # Resume logic and downstream notifications key off this column.
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class ReflectionLevel(str, PyEnum):
    DESCRIPTIVE = "descriptive"
    BASIC = "basic"
    DEEP = "deep"
    EXCEPTIONAL = "exceptional"


class CalibrationLevel(str, PyEnum):
    MISALIGNED = "misaligned"
    PARTIAL = "partial"
    ALIGNED = "aligned"


class TransferLevel(str, PyEnum):
    LACKING = "lacking"
    VAGUE = "vague"
    MEANINGFUL = "meaningful"


class NotificationType(str, PyEnum):
    UNFINISHED_ACTIVITY = "unfinished_activity"
    NEW_ACTIVITY = "new_activity"
    DEADLINE_REMINDER = "deadline_reminder"
    DEADLINE_SUMMARY = "deadline_summary"


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    activity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reflection_activities.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    deep_link: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )


class SessionMetric(Base):
    __tablename__ = "session_metrics"
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chat_sessions.id"), primary_key=True)

    # Reflection Quality
    reflection_quality_level: Mapped[str] = mapped_column(String(20), nullable=True)
    reflection_quality_justification: Mapped[str] = mapped_column(Text, nullable=True)
    reflection_quality_evidence: Mapped[list] = mapped_column(JSON, nullable=True)
    reflection_quality_action: Mapped[str] = mapped_column(Text, nullable=True)

    # Calibration between perception and performance
    calibration_level: Mapped[str] = mapped_column(String(20), nullable=True)
    calibration_justification: Mapped[str] = mapped_column(Text, nullable=True)
    calibration_evidence: Mapped[list] = mapped_column(JSON, nullable=True)
    calibration_action: Mapped[str] = mapped_column(Text, nullable=True)

    # Contextual Transfer
    contextual_transfer_level: Mapped[str] = mapped_column(String(20), nullable=True)
    contextual_transfer_justification: Mapped[str] = mapped_column(Text, nullable=True)
    contextual_transfer_evidence: Mapped[list] = mapped_column(JSON, nullable=True)
    contextual_transfer_action: Mapped[str] = mapped_column(Text, nullable=True)

    # Phase 5: per-session policy-engine telemetry (MetricsCollector.snapshot()).
    policy_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
