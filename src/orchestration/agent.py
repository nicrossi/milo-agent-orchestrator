import asyncio
import logging
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.clients.chat_history import ChatHistoryRepository
from src.adapters.clients.rag import RAGClient
from src.adapters.llm.gemini import GeminiAdapter

logger = logging.getLogger("milo-orchestrator.agent")

_NO_CONTEXT_MSG = "I couldn't find any relevant documents in my database to answer that."


class OrchestratorAgent:
    """
    High-level orchestrator that coordinates the database, RAG service, and LLM.
    """

    def __init__(self) -> None:
        self.rag_client = RAGClient()
        self.llm_adapter = GeminiAdapter()
        self.history_repo = ChatHistoryRepository()

    # ── Public API ────────────────────────────────────────────────────
    async def process_query(
            self,
            query: str,
            history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Stateless RAG pipeline (no session persistence)."""
        logger.info("Processing query: %s", query)
        context_chunks = await self.rag_client.retrieve_context(query)
        if not context_chunks:
            logger.info("No context retrieved. Bypassing LLM execution.")
            return _NO_CONTEXT_MSG

        return self.llm_adapter.generate_answer(query, context_chunks, history)

    async def process_session_stream(
            self,
            db: AsyncSession,
            session_id: str,
            query: str,
    ) -> AsyncIterator[str]:
        """Session-aware RAG + LLM streaming pipeline."""
        history = await self._load_history(db, session_id)
        await self._persist_user_message(db, session_id, query)

        context_chunks = await self.rag_client.retrieve_context(query)
        if not context_chunks:
            yield await self._handle_no_context(db, session_id)
            return

        async for chunk in self._stream_and_persist(db, session_id, query, context_chunks, history):
            yield chunk

    async def _load_history(self, db: AsyncSession, session_id: str) -> List[Dict[str, str]]:
        """Load prior conversation turns from the database."""
        history = await self.history_repo.get_history(db, session_id)
        logger.info("Session '%s': loaded %d previous messages.", session_id, len(history))
        return history

    async def _persist_user_message(self, db: AsyncSession, session_id: str, query: str) -> None:
        """Persist the incoming user turn before calling any external service."""
        await self.history_repo.save_message(db, session_id, "user", query)
        await db.commit()

    async def _handle_no_context(self, db: AsyncSession, session_id: str) -> str:
        """Persist and return the fallback when RAG yields nothing."""
        logger.info("Session '%s': no RAG context — returning fallback.", session_id)
        await self.history_repo.save_message(db, session_id, "model", _NO_CONTEXT_MSG)
        await db.commit()
        return _NO_CONTEXT_MSG

    async def _stream_and_persist(
            self,
            db: AsyncSession,
            session_id: str,
            query: str,
            context_chunks: List[str],
            history: List[Dict[str, str]],
    ) -> AsyncIterator[str]:
        """Stream the LLM response and guarantee persistence."""

        collected: List[str] = []
        interrupted = False

        try:
            async for chunk in self.llm_adapter.generate_answer_stream(query, context_chunks, history):
                collected.append(chunk)
                yield chunk
        except asyncio.CancelledError:
            interrupted = True
            logger.info("Session '%s': client disconnected mid-stream.", session_id)
            raise
        finally:
            await self._persist_model_response(db, session_id, collected, interrupted)

    async def _persist_model_response(
            self,
            db: AsyncSession,
            session_id: str,
            parts: List[str],
            interrupted: bool,
    ) -> None:
        """Persist the complete (or partial) model response."""

        if not parts:
            return

        response = "".join(parts)
        if interrupted:
            response += " [Interrupted]"

        try:
            await self.history_repo.save_message(db, session_id, "model", response)
            await db.commit()
            logger.info("Session '%s': saved model response (%d chars).",
                        session_id, len(response))
        except Exception:
            logger.error(
                "Session '%s': failed to save model response to DB.",
                session_id, exc_info=True)
