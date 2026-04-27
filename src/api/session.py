"""
Encapsulates WebSocket connection lifecycle and chat session state.
"""

import asyncio
import logging
import time
from typing import Optional, List
import uuid

from fastapi import WebSocket, BackgroundTasks
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.core.database import get_db_session
from src.core.models import ChatSession as ChatSessionModel, SessionStatus, SessionMetric, ReflectionActivity
from src.orchestration.agent import OrchestratorAgent
from src.policy.engine import PolicyEngine
from src.policy.metrics import MetricsCollector
from src.policy.persistence import PolicyStateSnapshot
from src.policy.signals.aggregator import build_user_signals, message_word_count
from src.policy.types import (
    ActivityRef,
    FSMState,
    HintLadderState,
    PolicyContext,
    RecoveryState,
    UserSignals,
)
from src.services.metrics_evaluator import evaluate_session

logger = logging.getLogger("milo-orchestrator.session")

_IDLE_TIMEOUT_SECONDS = 3600.0
_policy_engine = PolicyEngine()  # stateless singleton — shared across all sessions


async def run_llm_evaluator(session_id: uuid.UUID, agent: OrchestratorAgent,) -> None:
    try:
        if not agent:
            logger.error("No agent available for evaluation.")
            return

        await evaluate_session(session_id, agent)
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
        # Phase 3: full activity ref (title + teacher_goal + context_description),
        # built once at setup and passed into every PolicyContext.
        self._activity_ref: Optional[ActivityRef] = None
        self._created_tasks: List[asyncio.Task] = []
        # Policy state — in-memory for the lifetime of this WebSocket connection.
        self._fsm_state: FSMState = FSMState.PLANNING
        self._recent_question_ids: List[str] = []
        self._last_question_text: str = ""
        # Phase 1: rolling signals + supporting windows for z-score extractors.
        # Window cap (10) keeps memory bounded and z-scores reactive to recent state.
        self._signals_window: List[UserSignals] = []
        self._length_window: List[int] = []
        self._latency_window: List[float] = []
        self._last_milo_response_ts: Optional[float] = None
        # Phase 4: cross-turn ladder, recovery, and cooldown state.
        self._hint_state: HintLadderState = HintLadderState.PROCESS_FEEDBACK
        self._turns_in_hint_state: int = 0
        self._consecutive_low_struggle_turns: int = 0
        self._recovery_state: RecoveryState = RecoveryState.NORMAL
        self._turns_in_recovery: int = 0
        self._turns_since_meta_feedback: int = 99
        # Phase 5: per-session policy metrics collector.
        self._metrics = MetricsCollector()

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
        except (ValueError, AttributeError):
            # Non-UUID activity_id (e.g. mock IDs like "c1") — run without DB persistence.
            logger.warning(
                "activity_id '%s' is not a valid UUID — running in stateless mode.",
                self._activity_id,
            )
            self._session_id_uuid = None
            self._session_id = str(uuid.uuid4())
            return True

        try:
            async with get_db_session() as db:
                # Phase 5: try to resume a recent session for this user+activity
                # before creating a new one. Resume any session that has a
                # persisted policy_state within the cutoff window, regardless
                # of evaluation status — the student returning to continue is
                # the same UX whether the prior eval succeeded, failed, or is
                # still pending. On resume we flip status back to IN_PROGRESS.
                from datetime import datetime, timedelta, timezone
                from sqlalchemy import select

                resume_cutoff = datetime.now(timezone.utc) - timedelta(minutes=60)
                resumable_stmt = (
                    select(ChatSessionModel)
                    .where(ChatSessionModel.activity_id == activity_uuid)
                    .where(ChatSessionModel.student_id == self._user_id)
                    .where(ChatSessionModel.policy_state.is_not(None))
                    .where(ChatSessionModel.started_at >= resume_cutoff)
                    .order_by(ChatSessionModel.started_at.desc())
                    .limit(1)
                )
                existing = (await db.execute(resumable_stmt)).scalar_one_or_none()

                if existing:
                    db_session = existing
                    db_session.status = SessionStatus.IN_PROGRESS  # reset lifecycle
                    await db.commit()
                    logger.info(
                        "Session '%s': resuming prior session for user=%s activity=%s "
                        "(prior status=%s).",
                        existing.id, self._user_id, activity_uuid, existing.status,
                    )
                else:
                    db_session = ChatSessionModel(
                        activity_id=activity_uuid,
                        student_id=self._user_id,
                        status=SessionStatus.IN_PROGRESS,
                    )
                    db.add(db_session)
                    await db.commit()

                self._session_id_uuid = db_session.id
                self._session_id = str(db_session.id)

                if activity := await db.get(ReflectionActivity, activity_uuid):
                    self._context_description = activity.context_description
                    # Phase 3: build a richer ref so the policy engine can
                    # contextualize questions with the activity title.
                    self._activity_ref = ActivityRef(
                        id=str(activity.id),
                        title=activity.title or "",
                        teacher_goal=activity.teacher_goal or "",
                        context_description=activity.context_description or "",
                    )

                # Phase 5: rehydrate policy state if a snapshot exists from a
                # prior connection. Best-effort; on any failure we start fresh.
                if snapshot := PolicyStateSnapshot.deserialize(db_session.policy_state):
                    snapshot.apply_to(self)
                    logger.info(
                        "Session '%s': policy_state restored from prior connection.",
                        self._session_id,
                    )
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
                    # Phase 5: snapshot the final policy_state for resumption
                    # (brief window before LLM evaluator marks EVALUATED).
                    session.policy_state = PolicyStateSnapshot.from_session(self).serialize()
                    await db.commit()

                # Phase 5: write per-session policy metrics to SessionMetric.
                metric_row = await db.get(SessionMetric, self._session_id_uuid)
                if metric_row is None:
                    metric_row = SessionMetric(session_id=self._session_id_uuid)
                    db.add(metric_row)
                metric_row.policy_metrics = self._metrics.snapshot()
                await db.commit()

            # Non-blocking background task scheduling
            asyncio.create_task(run_llm_evaluator(self._session_id_uuid, self._agent))
        except Exception as e:
            logger.error(f"Failed to wrap up session {self._session_id}: {e}")

    async def _persist_policy_state(self) -> None:
        """Phase 5: write the current policy_state snapshot to the session row."""
        if not self._session_id_uuid:
            return
        try:
            snapshot = PolicyStateSnapshot.from_session(self).serialize()
            async with get_db_session() as db:
                if session := await db.get(ChatSessionModel, self._session_id_uuid):
                    session.policy_state = snapshot
                    await db.commit()
        except Exception as exc:
            # Persistence failure should never break the live conversation.
            logger.warning(
                "Session '%s': failed to persist policy_state — %s",
                self._session_id, exc,
            )

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
        # Policy loop — 6 steps:
        # 1. Load history to derive turn count (completed exchange pairs).
        # 2. Evaluate policy (FSM transition + question selection + rules).
        # 3. Stream LLM response with policy directives injected into context.
        # 4. Run output interceptors on the accumulated full response.
        # 5. Update in-memory policy state.
        # 6. Send "done" frame carrying policy metadata.
        # Capture user-message arrival time once for latency math.
        user_msg_ts = time.time()
        # Latency between previous Milo response and this user message.
        # None on first turn — extractors treat it as neutral.
        observed_latency: Optional[float] = (
            max(0.0, user_msg_ts - self._last_milo_response_ts)
            if self._last_milo_response_ts is not None
            else None
        )

        try:
            # Step 1: derive turn count from history length
            prompt_directives: List[str] = []
            decision = None
            try:
                async with get_db_session() as db:
                    history = await self._agent.history_repo.get_history(
                        db, self._user_id, self._session_id
                    )
                turn_count = len(history) // 2

                # Phase 1: build per-turn UserSignals from text + server timing.
                user_signals = build_user_signals(
                    user_message=user_text,
                    signals_window=self._signals_window,
                    prev_milo_response_ts=self._last_milo_response_ts,
                    now_ts=user_msg_ts,
                    length_window=self._length_window,
                    latency_window=self._latency_window,
                )

                # Step 2: evaluate policy
                ctx = PolicyContext(
                    current_state=self._fsm_state,
                    turn_count=turn_count,
                    recent_question_ids=self._recent_question_ids.copy(),
                    user_message=user_text,
                    user_signals=user_signals,
                    signals_window=self._signals_window.copy(),
                    activity=self._activity_ref,
                    hint_state=self._hint_state,
                    turns_in_hint_state=self._turns_in_hint_state,
                    consecutive_low_struggle_turns=self._consecutive_low_struggle_turns,
                    recovery_state=self._recovery_state,
                    turns_in_recovery=self._turns_in_recovery,
                    turns_since_meta_feedback=self._turns_since_meta_feedback,
                )
                decision = _policy_engine.evaluate(ctx)
                prompt_directives = decision.plan.prompt_directives

                # Append to rolling windows AFTER evaluate (so this turn's signals
                # don't pollute their own z-score computation).
                self._signals_window.append(user_signals)
                self._signals_window = self._signals_window[-10:]
                self._length_window.append(message_word_count(user_text))
                self._length_window = self._length_window[-10:]
                if observed_latency is not None:
                    self._latency_window.append(observed_latency)
                    self._latency_window = self._latency_window[-10:]

                scores_log = decision.scores.model_dump() if decision.scores else None
                logger.info(
                    "Session '%s': policy — %s→%s, q=%s, rules=%s, scores=%s",
                    self._session_id,
                    ctx.current_state.value,
                    decision.next_state.value,
                    decision.plan.question_id,
                    decision.applied_rules,
                    scores_log,
                )
            except Exception as exc:
                # Graceful degradation: log and continue with empty directives.
                logger.error(
                    "Session '%s': policy evaluate() failed — degrading gracefully. Error: %s",
                    self._session_id, exc, exc_info=True,
                )

            # Step 3: stream with directives injected
            accumulated: List[str] = []
            async with get_db_session() as db:
                stream = self._agent.process_session_stream(
                    db, self._user_id, self._session_id, user_text,
                    self._context_description, self._activity_id,
                    prompt_directives=prompt_directives,
                )
                async for chunk in stream:
                    accumulated.append(chunk)
                    if not await self._send_json({"type": "chunk", "text": chunk}):
                        logger.info(
                            "Session '%s': client dropped mid-stream - halting.", self._session_id
                        )
                        return

            # Step 4: output interception
            if decision is not None:
                full_response = "".join(accumulated)
                was_intercepted, final_text = _policy_engine.check_output(full_response, decision)
                if was_intercepted:
                    correction = final_text[len(full_response):]
                    await self._send_json({"type": "chunk", "text": correction})
                    logger.info(
                        "Session '%s': interceptor fired — correction appended.", self._session_id
                    )

                # Step 5: update policy state
                self._fsm_state = decision.next_state
                self._recent_question_ids.append(decision.plan.question_id)
                self._last_question_text = decision.plan.question_text
                # Phase 4: persist cross-turn state.
                self._hint_state = decision.next_hint_state
                self._turns_in_hint_state = decision.next_turns_in_hint_state
                self._consecutive_low_struggle_turns = decision.next_consecutive_low_struggle_turns
                self._recovery_state = decision.next_recovery_state
                self._turns_in_recovery = decision.next_turns_in_recovery
                self._turns_since_meta_feedback = decision.next_turns_since_meta_feedback
                # Phase 5: capture per-turn metrics + interceptor outcome.
                self._metrics.record_decision(decision)
                self._metrics.record_interceptor_correction(was_intercepted)
                # Phase 5: persist policy_state snapshot for resumption.
                await self._persist_policy_state()

                # Step 6: done with policy metadata
                await self._send_json({
                    "type": "done",
                    "policy": {
                        "state": decision.next_state.value,
                        "question_id": decision.plan.question_id,
                        "applied_rules": decision.applied_rules,
                        "hint_state": decision.next_hint_state.value,
                        "recovery_state": decision.next_recovery_state.value,
                    },
                })
            else:
                await self._send_json({"type": "done"})

            # Mark when this Milo response finished so the next turn's latency
            # can be measured correctly.
            self._last_milo_response_ts = time.time()

        except PermissionError:
            await self._send_error("No tenes permiso para acceder a esta conversacion.")
        except RuntimeError as exc:
            logger.error("Session '%s': agent error - %s", self._session_id, exc)
            await self._send_error(str(exc))
        except asyncio.CancelledError:
            logger.info("Session '%s': turn cancelled mid-flight.", self._session_id)
            raise
