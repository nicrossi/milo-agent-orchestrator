import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.clients.chat_history import ChatHistoryRepository
from src.adapters.llm.gemini import GeminiAdapter
from src.core.database import get_db_session
from src.schemas.chat import MessageDTO
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

    @staticmethod
    def _format_memory_block(memory_items: List[MessageDTO]) -> str:
        if not memory_items:
            return ""
        lines = ["[User Cross-Chat Memory]"]
        for item in memory_items:
            role = "User" if item.role == "user" else "Milo"
            content = str(item.content or "").strip()
            if not content:
                continue
            lines.append(f"{role}: {content}")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    def _compose_context(
        self,
        rag_chunks: List[str],
        cross_chat_memory: Optional[List[MessageDTO]] = None,
        context_description: Optional[str] = None,
    ) -> List[str]:
        # Always include Milo identity + behavior instructions.
        chunks: List[str] = [self.base_context]
        if context_description:
            chunks.append(f"The student is reflecting on: {context_description}")
        memory_block = self._format_memory_block(cross_chat_memory or [])
        if memory_block:
            chunks.append(memory_block)
        chunks.extend(rag_chunks)
        return chunks

    async def process_query(
        self,
        query: str,
        history: Optional[List[MessageDTO]] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Stateless RAG pipeline."""
        logger.info("Processing stateless query for user=%s", user_id or "<none>")
        async with get_db_session() as db:
            rag_chunks = await self.rag_service.retrieve_context(db, query, user_id=user_id)
        context_chunks = self._compose_context(rag_chunks, [])
        return self.llm_adapter.generate_answer(query, context_chunks, history)

    def generate_evaluation(self, prompt: str) -> str:
        """Generate evaluation of a session using the underlying LLM adapter."""
        return self.llm_adapter.generate_evaluation(prompt)

    async def process_session_stream(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        query: str,
        context_description: Optional[str] = None
    ) -> AsyncIterator[str]:
        """Session-aware RAG + LLM streaming pipeline with user isolation."""
        # Note: Ownership is determined by chat_sessions, so we avoid bind_or_validate_session_owner.
        history = await self._load_history(db, user_id, session_id)
        cross_chat_memory = await self.history_repo.get_recent_cross_session_memory(
            db, user_id, session_id, limit=12
        )
        if query: # Only persist and query if user sends a message. Greeting uses empty query.
            await self._persist_user_message(db, user_id, session_id, query)

        rag_chunks = await self.rag_service.retrieve_context(db, query, user_id=user_id) if query else []
        context_chunks = self._compose_context(rag_chunks, cross_chat_memory, context_description)

        real_query = query if query else f"Hi there! Initiate conversation based on the context."

        async for chunk in self._stream_and_persist(
            db, user_id, session_id, real_query, context_chunks, history
        ):
            yield chunk

    async def _load_history(
        self, db: AsyncSession, user_id: str, session_id: str
    ) -> List[MessageDTO]:
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
        history: List[MessageDTO],
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

_agent_instance = None

def get_agent() -> OrchestratorAgent | None:
    """FastAPI Dependency that provides the OrchestratorAgent."""
    global _agent_instance

    if _agent_instance is not None:
        return _agent_instance

    try:
        from src.main import rag_service

        _agent_instance = OrchestratorAgent(rag_service=rag_service)
        return _agent_instance

    except Exception as err:
        logger.error("CRITICAL: OrchestratorAgent failed to initialise: %s", err, exc_info=True)