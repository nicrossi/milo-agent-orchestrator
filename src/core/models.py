import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
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

    # This composite index perfectly covers the query used in `chat_history.py`:
    # `WHERE session_id = ? ORDER BY created_at DESC LIMIT ?`
    # This prevents full table scans as the database grows over time.
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


class ChatSessionOwnership(Base):
    """
    Binds each session_id to exactly one Firebase user.
    Prevents cross-user access to the same chat thread identifier.
    """

    __tablename__ = "chat_session_ownership"

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_chat_session_ownership_user_id", "user_id"),)
