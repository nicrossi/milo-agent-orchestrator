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


class ActivityStatus(str, PyEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class ReflectionActivity(Base):
    __tablename__ = "reflection_activities"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher_goal: Mapped[str] = mapped_column(Text, nullable=False)
    context_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ActivityStatus] = mapped_column(String(50), default=ActivityStatus.PUBLISHED)
    created_by_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)


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
