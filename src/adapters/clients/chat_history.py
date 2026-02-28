import asyncio
import logging
from typing import List, Dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.models import ChatMessage

logger = logging.getLogger("milo-orchestrator.chat_history")


class ChatHistoryRepository:
    """Data-access layer for persisting and retrieving chat messages."""

    @staticmethod
    async def get_history(
            session: AsyncSession,
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
                .where(ChatMessage.session_id == session_id)
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
    async def save_message(
            session: AsyncSession,
            session_id: str,
            role: str,
            content: str,
    ) -> ChatMessage:
        """Persist a single chat message and flush so it gets a timestamp."""
        try:
            msg = ChatMessage(session_id=session_id, role=role, content=content)
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