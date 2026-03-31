import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
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


class SessionStatus(str, PyEnum):
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_EVALUATION = "PENDING_EVALUATION"
    EVALUATED = "EVALUATED"
    EVALUATION_FAILED = "EVALUATION_FAILED"


class ActivityStatus(str, PyEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class GoalAlignment(str, PyEnum):
    ACHIEVED = "Achieved"
    PARTIAL = "Partially Achieved"
    NOT_ACHIEVED = "Not Achieved"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)


class ReflectionActivity(Base):
    __tablename__ = "reflection_activities"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher_goal: Mapped[str] = mapped_column(Text, nullable=False)
    context_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ActivityStatus] = mapped_column(String(50), default=ActivityStatus.PUBLISHED)
    created_by_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("reflection_activities.id"), nullable=False)
    student_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(String(50), default=SessionStatus.IN_PROGRESS)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SessionMetric(Base):
    __tablename__ = "session_metrics"
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chat_sessions.id"), primary_key=True)
    dors_level: Mapped[str] = mapped_column(String(255), nullable=True)
    dors_score: Mapped[int] = mapped_column(nullable=True)
    goal_status: Mapped[GoalAlignment] = mapped_column(String(50), nullable=True)
    goal_score: Mapped[int] = mapped_column(nullable=True)
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=True)
