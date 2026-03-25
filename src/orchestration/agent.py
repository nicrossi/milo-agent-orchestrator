import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy import text
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

    @staticmethod
    def _format_memory_block(memory_items: List[Dict[str, str]]) -> str:
        if not memory_items:
            return ""
        lines = ["[User Cross-Chat Memory]"]
        for item in memory_items:
            role = "User" if item.get("role") == "user" else "Milo"
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"{role}: {content}")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    @staticmethod
    def _format_user_profile_block(profile: Dict[str, str]) -> str:
        if not profile:
            return ""

        lines = ["[Current User Profile]"]
        if profile.get("name"):
            lines.append(f"Name: {profile['name']}")
        if profile.get("email"):
            lines.append(f"Email: {profile['email']}")
        if profile.get("roles"):
            lines.append(f"Roles: {profile['roles']}")
        if profile.get("user_id"):
            lines.append(f"User ID: {profile['user_id']}")

        return "\n".join(lines) if len(lines) > 1 else ""

    async def _load_user_profile(self, db: AsyncSession, user_id: Optional[str]) -> Dict[str, str]:
        if not user_id:
            return {}

        try:
            result = await db.execute(
                text(
                    """
                    SELECT
                        u.id AS user_id,
                        COALESCE(NULLIF(u.display_name, ''), '') AS display_name,
                        COALESCE(NULLIF(u.email, ''), '') AS email,
                        COALESCE(
                            STRING_AGG(DISTINCT r.code, ', ' ORDER BY r.code)
                            FILTER (WHERE r.code IS NOT NULL),
                            ''
                        ) AS roles
                    FROM users u
                    LEFT JOIN user_roles ur ON ur.user_id = u.id
                    LEFT JOIN roles r ON r.id = ur.role_id
                    WHERE u.id = :user_id
                    GROUP BY u.id, u.display_name, u.email
                    """
                ),
                {"user_id": user_id},
            )
            row = result.mappings().first()
            if not row:
                return {"user_id": user_id}

            name = str(row.get("display_name") or "").strip()
            email = str(row.get("email") or "").strip()
            inferred_name = name or (email.split("@")[0] if email else "")

            return {
                "user_id": str(row.get("user_id") or user_id),
                "name": inferred_name,
                "email": email,
                "roles": str(row.get("roles") or "").strip(),
            }
        except Exception:
            # Important: if this query fails, asyncpg marks the transaction as failed.
            # We must rollback before continuing with other queries on this session.
            await db.rollback()
            logger.warning("Could not load extended user profile context for user=%s", user_id)
            return {"user_id": user_id}

    def _compose_context(
        self,
        rag_chunks: List[str],
        user_profile: Optional[Dict[str, str]] = None,
        cross_chat_memory: Optional[List[Dict[str, str]]] = None,
    ) -> List[str]:
        # Always include Milo identity + behavior instructions.
        chunks: List[str] = [self.base_context]
        profile_block = self._format_user_profile_block(user_profile or {})
        if profile_block:
            chunks.append(profile_block)
        memory_block = self._format_memory_block(cross_chat_memory or [])
        if memory_block:
            chunks.append(memory_block)
        chunks.extend(rag_chunks)
        return chunks

    async def process_query(
        self,
        query: str,
        history: Optional[List[Dict[str, str]]] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Stateless RAG pipeline."""
        logger.info("Processing stateless query for user=%s", user_id or "<none>")
        async with get_db_session() as db:
            user_profile = await self._load_user_profile(db, user_id)
            rag_chunks = await self.rag_service.retrieve_context(db, query, user_id=user_id)
        context_chunks = self._compose_context(rag_chunks, user_profile, [])
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
        user_profile = await self._load_user_profile(db, user_id)
        cross_chat_memory = await self.history_repo.get_recent_cross_session_memory(
            db, user_id, session_id, limit=12
        )
        await self._persist_user_message(db, user_id, session_id, query)

        rag_chunks = await self.rag_service.retrieve_context(db, query, user_id=user_id)
        context_chunks = self._compose_context(rag_chunks, user_profile, cross_chat_memory)

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
