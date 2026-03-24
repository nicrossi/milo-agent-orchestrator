import asyncio
import logging
from typing import List, Dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.models import ChatMessage, ChatSessionOwnership

logger = logging.getLogger("milo-orchestrator.chat_history")


class ChatHistoryRepository:
    """Data-access layer for persisting and retrieving chat messages."""

    @staticmethod
    async def get_history(
            session: AsyncSession,
            user_id: str,
            session_id: str,
            limit: int = 50,
    ) -> List[Dict[str, str]]:
        """
        Load the most recent messages for a session, ordered chronologically.

        Returns a list of {"role": ..., "content": ...} dicts ready to be
        injected into the LLM prompt.
        """
        try:
            # Fetch the newest messages first to prevent amnesia
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id, ChatMessage.user_id == user_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

            # Reverse the rows in-memory using to maintain chronological.
            return [{"role": row.role, "content": row.content} for row in reversed(rows)]
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "DB error loading chat history for session %s:",
                session_id,
                exc_info=True,
            )
            raise

    @staticmethod
    async def get_history_records(
            session: AsyncSession,
            user_id: str,
            session_id: str,
            limit: int = 200,
    ) -> List[ChatMessage]:
        """Load chat rows for UI rendering (chronological order)."""
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.user_id == user_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_recent_cross_session_memory(
            session: AsyncSession,
            user_id: str,
            current_session_id: str,
            limit: int = 12,
    ) -> List[Dict[str, str]]:
        """
        Return recent messages from the same user across other sessions.
        Useful as lightweight long-term memory between chats.
        """
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.user_id == user_id,
                ChatMessage.session_id != current_session_id,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        rows = list(reversed(rows))
        return [{"role": row.role, "content": row.content} for row in rows]

    @staticmethod
    async def bind_or_validate_session_owner(
            session: AsyncSession,
            session_id: str,
            user_id: str,
    ) -> None:
        """
        Ensure a session belongs to exactly one user:
        - If unbound, bind it to current user.
        - If already bound to another user, deny access.
        """
        stmt = select(ChatSessionOwnership).where(ChatSessionOwnership.session_id == session_id)
        result = await session.execute(stmt)
        ownership = result.scalar_one_or_none()

        if ownership is None:
            session.add(ChatSessionOwnership(session_id=session_id, user_id=user_id))
            await session.flush()
            # Backfill legacy rows that pre-date user_id scoping.
            legacy_stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id, ChatMessage.user_id.is_(None))
            )
            legacy_rows = (await session.execute(legacy_stmt)).scalars().all()
            for row in legacy_rows:
                row.user_id = user_id
            return

        if ownership.user_id != user_id:
            raise PermissionError("This session belongs to another user.")

    @staticmethod
    async def validate_session_owner(
            session: AsyncSession,
            session_id: str,
            user_id: str,
    ) -> None:
        """
        Validate session ownership without auto-binding.
        If session is unknown, allow empty-history behavior.
        """
        stmt = select(ChatSessionOwnership).where(ChatSessionOwnership.session_id == session_id)
        result = await session.execute(stmt)
        ownership = result.scalar_one_or_none()
        if ownership is not None and ownership.user_id != user_id:
            raise PermissionError("This session belongs to another user.")

    @staticmethod
    async def save_message(
            session: AsyncSession,
            user_id: str,
            session_id: str,
            role: str,
            content: str,
    ) -> ChatMessage:
        """Persist a single chat message and flush so it gets a timestamp."""
        try:
            msg = ChatMessage(session_id=session_id, user_id=user_id, role=role, content=content)
            session.add(msg)
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
