import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.clients.chat_history import ChatHistoryRepository
from src.adapters.llm.gemini import GeminiAdapter
from src.core.database import get_db_session
from src.services.rag import IntegratedRAGService

logger = logging.getLogger("milo-orchestrator.agent")
_FALLBACK_BASE_CONTEXT = (
    "You are Milo, an AI metacognitive coach for students. "
    "Your goal is to guide reflection through questions, not give direct final answers. "
    "Help users clarify goals, monitor understanding, evaluate strategy, and transfer learning. "
    "Be warm, concise, and practical."
)


class OrchestratorAgent:
    """
    High-level orchestrator that coordinates the database, RAG service, and LLM.
    """

    def __init__(self, rag_service: IntegratedRAGService) -> None:
        self.rag_service = rag_service
        self.llm_adapter = GeminiAdapter()
        self.history_repo = ChatHistoryRepository()
        self.base_context = self._load_base_context()

    @staticmethod
    def _load_base_context() -> str:
        prompt_file = Path(__file__).resolve().parents[1] / "prompts" / "milo_base_context.md"
        try:
            text = prompt_file.read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:
            logger.warning("Could not load milo_base_context.md; using inline fallback.")
        return _FALLBACK_BASE_CONTEXT

    def _compose_context(self, rag_chunks: List[str]) -> List[str]:
        # Always include Milo identity + behavior instructions.
        return [self.base_context, *rag_chunks]

    async def process_query(
        self,
        query: str,
        history: Optional[List[Dict[str, str]]] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Stateless RAG pipeline."""
        logger.info("Processing stateless query for user=%s", user_id or "<none>")
        async with get_db_session() as db:
            rag_chunks = await self.rag_service.retrieve_context(db, query, user_id=user_id)
        context_chunks = self._compose_context(rag_chunks)
        return self.llm_adapter.generate_answer(query, context_chunks, history)

    async def process_session_stream(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        query: str,
    ) -> AsyncIterator[str]:
        """Session-aware RAG + LLM streaming pipeline with user isolation."""
        await self.history_repo.bind_or_validate_session_owner(db, session_id, user_id)
        history = await self._load_history(db, user_id, session_id)
        await self._persist_user_message(db, user_id, session_id, query)

        rag_chunks = await self.rag_service.retrieve_context(db, query, user_id=user_id)
        context_chunks = self._compose_context(rag_chunks)

        async for chunk in self._stream_and_persist(
            db, user_id, session_id, query, context_chunks, history
        ):
            yield chunk

    async def _load_history(
        self, db: AsyncSession, user_id: str, session_id: str
    ) -> List[Dict[str, str]]:
        history = await self.history_repo.get_history(db, user_id, session_id)
        logger.info(
            "Session '%s': loaded %d previous messages for user=%s.",
            session_id,
            len(history),
            user_id,
        )
        return history

    async def _persist_user_message(
        self, db: AsyncSession, user_id: str, session_id: str, query: str
    ) -> None:
        await self.history_repo.save_message(db, user_id, session_id, "user", query)
        await db.commit()

    async def _stream_and_persist(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        query: str,
        context_chunks: List[str],
        history: List[Dict[str, str]],
    ) -> AsyncIterator[str]:
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
            await self._persist_model_response(db, user_id, session_id, collected, interrupted)

    async def _persist_model_response(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        parts: List[str],
        interrupted: bool,
    ) -> None:
        if not parts:
            return

        response = "".join(parts)
        if interrupted:
            response += " [Interrupted]"

        try:
            await self.history_repo.save_message(db, user_id, session_id, "model", response)
            await db.commit()
            logger.info("Session '%s': saved model response (%d chars).", session_id, len(response))
        except Exception:
            logger.error(
                "Session '%s': failed to save model response to DB.",
                session_id,
                exc_info=True,
            )
