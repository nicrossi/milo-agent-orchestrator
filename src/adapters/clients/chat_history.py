import asyncio
import logging
import uuid
from typing import List, Dict, Union, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.models import ChatMessage, ChatSession
from src.schemas.chat import MessageDTO, UIMessageDTO

logger = logging.getLogger("milo-orchestrator.chat_history")

SessionID = Union[str, uuid.UUID]

class ChatHistoryRepository:
    """Data-access layer for persisting and retrieving chat messages."""

    @classmethod
    def _to_uuid(cls, session_id: SessionID) -> Optional[uuid.UUID]:
        """Helper method to safely coerce a string to a UUID."""
        if isinstance(session_id, uuid.UUID):
            return session_id
        try:
            return uuid.UUID(session_id)
        except (ValueError, TypeError, AttributeError):
            return None

    @classmethod
    async def get_history(
            cls,
            session: AsyncSession,
            user_id: str,
            session_id: str,
            limit: int = 50,
    ) -> List[MessageDTO]:
        """
        Load the most recent messages for a session, ordered chronologically.

        Returns a list of MessageDTOs ready to be injected into the LLM prompt.
        """
        try:
            session_uuid = cls._to_uuid(session_id)
            if not session_uuid:
                return []

            # Fetch the newest messages first to prevent amnesia
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == session_uuid, ChatMessage.user_id == user_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            # Reverse the rows in-memory using to maintain chronological order.
            return [MessageDTO(role=row.role, content=row.content) for row in reversed(rows)]
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "DB error loading chat history for session %s:",
                session_id,
                exc_info=True,
            )
            raise

    @classmethod
    async def get_history_records(
            cls,
            session: AsyncSession,
            user_id: str,
            session_id: str,
            limit: int = 200,
    ) -> List[UIMessageDTO]:
        """Load chat rows for UI rendering (chronological order)."""
        session_uuid = cls._to_uuid(session_id)
        if not session_uuid:
            return []

        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_uuid, ChatMessage.user_id == user_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return [
            UIMessageDTO(
                id=row.id,
                session_id=row.session_id,
                role=row.role,
                content=row.content,
                created_at=row.created_at,
            )
            for row in result.scalars().all()
        ]

    @classmethod
    async def get_recent_cross_session_memory(
            cls,
            session: AsyncSession,
            user_id: str,
            current_session_id: str,
            limit: int = 12,
            activity_id: Optional[str] = None,
    ) -> List[MessageDTO]:
        """
        Return recent messages from the same user across other sessions.
        Useful as lightweight long-term memory between chats.
        """
        current_session_uuid = cls._to_uuid(current_session_id)
        if not current_session_uuid:
            return []

        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.user_id == user_id,
                ChatMessage.session_id != current_session_uuid,
            )
        )

        if activity_id:
            activity_uuid = cls._to_uuid(activity_id)
            if activity_uuid:
                stmt = stmt.join(ChatSession, ChatMessage.session_id == ChatSession.id).where(ChatSession.activity_id == activity_uuid)

        stmt = stmt.order_by(ChatMessage.created_at.desc()).limit(limit)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        rows = list(reversed(rows))
        return [MessageDTO(role=row.role, content=row.content) for row in rows]
        
    @classmethod
    async def get_session_messages_for_eval(
            cls,
            session: AsyncSession,
            session_id: uuid.UUID,
    ) -> List[MessageDTO]:
        """Load completely all chronologically ordered messages for evaluation purposes."""
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc())
        )
        result = await session.execute(stmt)
        return [MessageDTO(role=row.role, content=row.content) for row in result.scalars().all()]

    @classmethod
    async def get_activity_messages_for_eval(
            cls,
            session: AsyncSession,
            activity_id: str,
            user_id: str,
    ) -> List[MessageDTO]:
        """Load completely all chronologically ordered messages for evaluation purposes for an activity."""
        stmt = (
            select(ChatMessage)
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(ChatSession.activity_id == activity_id, ChatMessage.user_id == user_id)
            .order_by(ChatMessage.created_at.asc())
        )
        result = await session.execute(stmt)
        return [MessageDTO(role=row.role, content=row.content) for row in result.scalars().all()]


    @classmethod
    async def validate_session_owner(
            cls,
            session: AsyncSession,
            session_id: str,
            user_id: str,
    ) -> None:
        """
        Validate session ownership without auto-binding.
        If session is unknown, allow empty-history behavior.
        """
        session_uuid = cls._to_uuid(session_id)
        if not session_uuid:
            return

        stmt = select(ChatSession).where(ChatSession.id == session_uuid)
        result = await session.execute(stmt)
        ownership = result.scalar_one_or_none()
        if ownership is not None and getattr(ownership, "student_id", None) != user_id:
            raise PermissionError("This session belongs to another user.")

    @classmethod
    async def save_message(
            cls,
            session: AsyncSession,
            user_id: str,
            session_id: str,
            role: str,
            content: str,
    ) -> ChatMessage:
        """Persist a single chat message and flush so it gets a timestamp."""
        try:
            session_uuid = cls._to_uuid(session_id)
            if not session_uuid:
                raise ValueError("Invalid session_id; must be a UUID string or UUID")

            msg = ChatMessage(session_id=session_uuid, user_id=user_id, role=role, content=content)
            session.add(msg)
            
            chat_session = await session.get(ChatSession, session_uuid)
            if chat_session:
                speaker = "Student" if role.lower() == "user" else "Milo"
                text_block = f"{speaker}: {content.strip()}\n\n"
                
                if chat_session.transcript is None:
                    chat_session.transcript = text_block
                else:
                    chat_session.transcript += text_block
                session.add(chat_session)

            await session.flush()
            return msg
        except asyncio.CancelledError:
            logger.info("DB save cancelled for %s message in session %s", role, session_id)
            raise
        except Exception:
            logger.error(
                "DB error saving %s message for session %s:",
                role,
                session_id,
                exc_info=True,
            )
            raise
