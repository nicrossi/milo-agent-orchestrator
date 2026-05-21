import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator, Callable, Dict, List, Optional

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

# Appended as a prompt directive when the student is in a voice session so
# replies feel like a back-and-forth conversation, not a lecture: short,
# conversational, single follow-up question. Pairs with a smaller
# max_output_tokens in the LLM adapter to keep first-audio latency low.
_VOICE_STYLE_DIRECTIVE = (
    "Response style for THIS turn — the student is speaking, not typing: "
    "keep your reply to 1–2 short sentences, conversational tone, no bullet "
    "lists or headings, end with at most one open follow-up question. "
    "Avoid long preambles or restating what the student just said."
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

    def _static_prefix(
        self,
        context_description: Optional[str] = None,
        teacher_goal: Optional[str] = None,
    ) -> str:
        # Session-stable prefix: Milo persona + activity context. Eligible for
        # Gemini context caching so the LLM doesn't re-process it every turn.
        parts: List[str] = [self.base_context]
        if context_description:
            parts.append(f"The student is reflecting on: {context_description}")
        if teacher_goal:
            parts.append(
                "Activity pedagogical goal (what the student should ultimately reach): "
                f"{teacher_goal}"
            )
        return "\n\n".join(parts)

    def _dynamic_chunks(
        self,
        rag_chunks: List[str],
        cross_chat_memory: Optional[List[MessageDTO]] = None,
        prompt_directives: Optional[List[str]] = None,
    ) -> List[str]:
        # Per-turn chunks — never cached. Memory rolls, RAG retrieves fresh,
        # directives come from the policy engine each turn.
        chunks: List[str] = []
        memory_block = self._format_memory_block(cross_chat_memory or [])
        if memory_block:
            chunks.append(memory_block)
        chunks.extend(rag_chunks)
        if prompt_directives:
            # Injected last for salience; populated by PolicyEngine.evaluate()
            chunks.append("\n".join(prompt_directives))
        return chunks

    def _compose_context(
        self,
        rag_chunks: List[str],
        cross_chat_memory: Optional[List[MessageDTO]] = None,
        context_description: Optional[str] = None,
        prompt_directives: Optional[List[str]] = None,
        teacher_goal: Optional[str] = None,
    ) -> List[str]:
        # Backwards-compatible composition (non-streaming path). Streaming path
        # uses _static_prefix + _dynamic_chunks directly so the static piece
        # can be sent via Gemini's cached_content.
        return [
            self._static_prefix(context_description, teacher_goal),
            *self._dynamic_chunks(rag_chunks, cross_chat_memory, prompt_directives),
        ]

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

    async def generate_evaluation(self, static_prefix: str, dynamic_suffix: str) -> str:
        """Generate evaluation of a session using the underlying LLM adapter."""
        return await self.llm_adapter.generate_evaluation(static_prefix, dynamic_suffix)

    async def process_session_stream(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        query: str,
        context_description: Optional[str] = None,
        activity_id: Optional[str] = None,
        prompt_directives: Optional[List[str]] = None,
        teacher_goal: Optional[str] = None,
        interrupt_check: Optional[Callable[[], bool]] = None,
        mode: str = "text",
    ) -> AsyncIterator[str]:
        """Session-aware RAG + LLM streaming pipeline with user isolation."""
        # Note: Ownership is determined by chat_sessions, so we avoid bind_or_validate_session_owner.
        history = await self._load_history(db, user_id, session_id)
        cross_chat_memory = await self.history_repo.get_recent_cross_session_memory(
            db, user_id, session_id, limit=12, activity_id=activity_id
        )

        rag_chunks = (
            await self.rag_service.retrieve_context(
                db, query, user_id=user_id, activity_id=activity_id
            )
            if query
            else []
        )
        is_voice = mode == "voice"
        # In voice mode, prepend a tone directive so replies stay short and
        # conversational. Goes through the existing prompt_directives channel
        # so the static prefix (cached) doesn't need to change.
        effective_directives = list(prompt_directives or [])
        if is_voice:
            effective_directives.insert(0, _VOICE_STYLE_DIRECTIVE)

        static_prefix = self._static_prefix(context_description, teacher_goal)
        dynamic_chunks = self._dynamic_chunks(
            rag_chunks, cross_chat_memory, effective_directives,
        )

        real_query = query if query else f"Hi there! Initiate conversation based on the context."

        async for chunk in self._stream_and_persist(
            db, user_id, session_id, real_query, query, dynamic_chunks, history,
            interrupt_check=interrupt_check,
            static_prefix=static_prefix,
            is_voice=is_voice,
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
        user_query: str,
        context_chunks: List[str],
        history: List[MessageDTO],
        interrupt_check: Optional[Callable[[], bool]] = None,
        static_prefix: Optional[str] = None,
        is_voice: bool = False,
    ) -> AsyncIterator[str]:
        collected: List[str] = []
        interrupted = False

        try:
            async for chunk in self.llm_adapter.generate_answer_stream(
                query, context_chunks, history,
                static_prefix=static_prefix,
                is_voice=is_voice,
            ):
                collected.append(chunk)
                yield chunk
        except asyncio.CancelledError:
            interrupted = True
            logger.info("Session '%s': stream cancelled mid-flight.", session_id)
            raise
        finally:
            if interrupt_check is not None and interrupt_check():
                # Interrupt-and-bundle path: the cancelled turn's text gets
                # carried forward by the session and re-persisted as part of
                # the next bundled turn. Skip persist here so we don't leave
                # an orphan [Interrupted] row or a stale user-message row.
                logger.info(
                    "Session '%s': skipping persist — turn interrupted by new user msg.",
                    session_id,
                )
            else:
                # Persist user msg first (kept out of pre-stream persistence so
                # an interrupted turn can leave zero rows). On disconnect
                # mid-stream we still persist so the transcript reflects what
                # the student sent.
                if user_query:
                    await self._persist_user_message(db, user_id, session_id, user_query)
                await self._persist_model_response(db, user_id, session_id, collected, interrupted)

    async def save_partial_response(
        self, db: AsyncSession, user_id: str, session_id: str, content: str
    ) -> None:
        """Persist a partial agent response (cancelled mid-stream).

        Called by the session's CancelledError handler when the turn was
        interrupted before _stream_and_persist completed its own save.
        """
        if not content.strip():
            return
        try:
            await self.history_repo.save_message(db, user_id, session_id, "model", content + " [Interrupted]")
            await db.commit()
            logger.info("Session '%s': saved partial (cancelled) response (%d chars).", session_id, len(content))
        except Exception:
            logger.error("Session '%s': failed to save partial response.", session_id, exc_info=True)

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