"""
Encapsulates WebSocket connection lifecycle and chat session state.
"""

import asyncio
import logging
from typing import Optional, List
import uuid

from fastapi import WebSocket, BackgroundTasks
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.core.database import get_db_session
from src.core.models import ChatSession as ChatSessionModel, SessionStatus, SessionMetric, GoalAlignment, ReflectionActivity
from src.orchestration.agent import OrchestratorAgent

logger = logging.getLogger("milo-orchestrator.session")

_IDLE_TIMEOUT_SECONDS = 3600.0


async def run_llm_evaluator(session_id: uuid.UUID):
    try:
        # TODO: Implement the actual LLM call using the generic prompt for MVP.
        # For now, simulate success and save placeholder metric.
        async with get_db_session() as db:
            session = await db.get(ChatSessionModel, session_id)
            if session:
                metric = SessionMetric(
                    session_id=session_id,
                    dors_level="Dialogic Reflection",
                    dors_score=75,
                    goal_status=GoalAlignment.ACHIEVED,
                    goal_score=80,
                    evidence_quote="The student demonstrated understanding."
                )
                db.add(metric)
                session.status = SessionStatus.EVALUATED
                await db.commit()
    except Exception as e:
        logger.error(f"Evaluating session {session_id} failed: {e}")
        async with get_db_session() as db:
            session = await db.get(ChatSessionModel, session_id)
            if session:
                session.status = SessionStatus.EVALUATION_FAILED
                await db.commit()


class ChatSession:
    """
    State wrapper around one WebSocket chat session.
    """

    def __init__(
        self, websocket: WebSocket, session_id: Optional[str], user_id: str, agent: OrchestratorAgent,
        activity_id: Optional[str] = None, background_tasks: Optional[BackgroundTasks] = None
    ) -> None:
        self._ws = websocket
        self._user_id = user_id
        self._agent = agent
        self._activity_id = activity_id
        self._background_tasks = background_tasks
        self._session_id_uuid = None
        self._session_id = None
        self._context_description = None
        self._created_tasks: List[asyncio.Task] = []

    async def run(self) -> None:
        """Main entry point for the WebSocket handler."""
        await self._ws.accept()

        if not await self._setup_db_session():
            return

        try:
            logger.info(
                "Session '%s': WebSocket connected for user=%s.",
                self._session_id, self._user_id
            )
            await self._process_turn("")
            await self._conversation_loop()
        except WebSocketDisconnect as exc:
            logger.info("Session '%s': client disconnected (code=%s).", self._session_id, exc.code)
        except asyncio.CancelledError:
            logger.info("Session '%s': handler cancelled.", self._session_id)
        except Exception as exc:
            logger.error("Session '%s': unhandled error - %s", self._session_id, exc, exc_info=True)
            await self._close(detail="An internal server error occurred.")
        finally:
            await self._wrap_up_session()

    async def _setup_db_session(self) -> bool:
        """Helper to initialize the database chat session."""
        try:
            activity_uuid = uuid.UUID(self._activity_id)
            async with get_db_session() as db:
                db_session = ChatSessionModel(
                    activity_id=activity_uuid,
                    student_id=self._user_id,
                    status=SessionStatus.IN_PROGRESS
                )
                db.add(db_session)
                await db.commit()

                self._session_id_uuid = db_session.id
                self._session_id = str(db_session.id)

                if activity := await db.get(ReflectionActivity, activity_uuid):
                    self._context_description = activity.context_description
            return True
        except Exception as e:
            logger.error(f"Failed to create chat session: {e}", exc_info=True)
            await self._close(detail="Failed to initialize chat session.")
            return False

    async def _wrap_up_session(self) -> None:
        """Helper to handle post-conversation database updates and tasks."""
        if not self._session_id_uuid:
            return

        try:
            async with get_db_session() as db:
                if session := await db.get(ChatSessionModel, self._session_id_uuid):
                    session.status = SessionStatus.PENDING_EVALUATION
                    await db.commit()

            # Non-blocking background task scheduling
            asyncio.create_task(run_llm_evaluator(self._session_id_uuid))
        except Exception as e:
            logger.error(f"Failed to wrap up session {self._session_id}: {e}")

    async def _conversation_loop(self) -> None:
        while True:
            user_text = await self._receive_message()
            if user_text is None:
                break

            if not user_text.strip():
                await self._send_error("Cannot process an empty message.")
                continue

            logger.info("Session '%s': received message.", self._session_id)
            await self._process_turn(user_text)

    async def _receive_message(self) -> Optional[str]:
        try:
            return await asyncio.wait_for(self._ws.receive_text(), timeout=_IDLE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("Session '%s': idle timeout exceeded - closing.", self._session_id)
            await self._ws.close(code=1008, reason="Idle timeout exceeded")
            return None

    async def _send_json(self, payload: dict) -> bool:
        if self._ws.client_state != WebSocketState.CONNECTED:
            logger.debug("Session '%s': suppressing send on closed socket.", self._session_id)
            return False
        try:
            await self._ws.send_json(payload)
            return True
        except Exception as exc:
            logger.warning("Session '%s': send failed - %s", self._session_id, exc)
            return False

    async def _send_error(self, detail: str) -> bool:
        return await self._send_json({"type": "error", "detail": detail})

    async def _close(self, *, detail: str, code: int = 1011) -> None:
        try:
            await self._send_error(detail)
            await self._ws.close(code=code, reason=detail[:123])
        except Exception:
            pass

    async def _process_turn(self, user_text: str) -> None:
        try:
            async with get_db_session() as db:
                stream = self._agent.process_session_stream(
                    db, self._user_id, self._session_id, user_text, self._context_description
                )
                async for chunk in stream:
                    if not await self._send_json({"type": "chunk", "text": chunk}):
                        logger.info(
                            "Session '%s': client dropped mid-stream - halting.", self._session_id
                        )
                        return

            await self._send_json({"type": "done"})
        except PermissionError:
            await self._send_error("No tenes permiso para acceder a esta conversacion.")
        except RuntimeError as exc:
            logger.error("Session '%s': agent error - %s", self._session_id, exc)
            await self._send_error(str(exc))
        except asyncio.CancelledError:
            logger.info("Session '%s': turn cancelled mid-flight.", self._session_id)
            raise
