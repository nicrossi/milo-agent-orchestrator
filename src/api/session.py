"""
Encapsulates WebSocket connection lifecycle and chat session state.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, List
import uuid

from fastapi import WebSocket, BackgroundTasks
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.core.database import get_db_session
from src.core.models import (
    ChatMessage,
    ChatSession as ChatSessionModel,
    ReflectionActivity,
    SessionMetric,
    SessionStatus,
)
from src.orchestration.agent import OrchestratorAgent
from src.policy.engine import CLOSURE_MIN_TURNS, CLOSURE_SENTINEL, PolicyEngine
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
from src.services.metrics_evaluator import queue_evaluation
from src.services.notifications import notify_unfinished_activity

logger = logging.getLogger("milo-orchestrator.session")

_IDLE_TIMEOUT_SECONDS = 3600.0
_policy_engine = PolicyEngine()  # stateless singleton — shared across all sessions


def _env_flag(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_AUTO_EVALUATE_ON_CHAT_CLOSE = _env_flag("AUTO_EVALUATE_ON_CHAT_CLOSE", default=True)


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
        # Set when the LLM emits CLOSURE_SENTINEL (and the turn-count guardrail
        # passes). Drives the conversation loop to break after this turn and
        # marks ChatSession.finalized_at.
        self._should_close: bool = False
        # Flipped to False in _setup_db_session when we resume an existing
        # session that already has a model greeting on disk. Suppresses the
        # speculative re-greeting that would otherwise overwrite the resumed
        # context for the student.
        self._needs_greeting: bool = True

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
            if self._needs_greeting:
                await self._process_turn("")
            else:
                # Resuming an existing session — the client speculatively
                # adds a streaming greeting bubble when it opens the WS.
                # Tell it to drop that bubble; the prior conversation is
                # already on screen (loaded from local cache) and we don't
                # want a fresh greeting layered on top.
                await self._send_json({
                    "type": "resumed",
                    "session_id": self._session_id,
                })
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
                # If the student already finalized this activity, the WS must
                # NOT open a new session and re-greet — the activity is closed
                # for them. Tell the client and bail.
                already_finalized_stmt = (
                    select(ChatSessionModel)
                    .where(ChatSessionModel.activity_id == activity_uuid)
                    .where(ChatSessionModel.student_id == self._user_id)
                    .where(ChatSessionModel.finalized_at.is_not(None))
                    .order_by(ChatSessionModel.finalized_at.desc())
                    .limit(1)
                )
                already_finalized = (
                    await db.execute(already_finalized_stmt)
                ).scalar_one_or_none()
                if already_finalized is not None:
                    logger.info(
                        "User=%s already finalized activity=%s on %s — refusing new session.",
                        self._user_id, activity_uuid, already_finalized.finalized_at,
                    )
                    await self._send_json({
                        "type": "already_finalized",
                        "session_id": str(already_finalized.id),
                        "finalized_at": already_finalized.finalized_at.isoformat(),
                    })
                    await self._ws.close(code=1000, reason="Activity already completed")
                    return False

                # Block entry to expired activities. We only check on WS
                # open: if the deadline elapses while the student is
                # already mid-conversation, we let them finish naturally
                # (cutting the socket would destroy their reflection
                # context). The teacher's deadline-summary worker treats
                # `finalized_at <= deadline` as the "completed on time"
                # criterion, so late finalizations are still recorded but
                # don't count toward the on-time cohort.
                activity_for_deadline = await db.get(ReflectionActivity, activity_uuid)
                if (
                    activity_for_deadline is not None
                    and activity_for_deadline.deadline is not None
                    and activity_for_deadline.deadline < datetime.now(timezone.utc)
                ):
                    logger.info(
                        "Activity=%s expired (deadline=%s); refusing entry for user=%s.",
                        activity_uuid, activity_for_deadline.deadline, self._user_id,
                    )
                    await self._send_json({
                        "type": "expired",
                        "deadline": activity_for_deadline.deadline.isoformat(),
                    })
                    await self._ws.close(code=1000, reason="Activity deadline elapsed")
                    return False

                # Resume the most recent non-finalized session for this
                # (user, activity), regardless of age. The LLM is the sole
                # judge of "done" via finalized_at — if it never fired, the
                # student is still mid-reflection and we want to preserve
                # their policy_state and transcript whenever they come back.
                resumable_stmt = (
                    select(ChatSessionModel)
                    .where(ChatSessionModel.activity_id == activity_uuid)
                    .where(ChatSessionModel.student_id == self._user_id)
                    .where(ChatSessionModel.finalized_at.is_(None))
                    .where(ChatSessionModel.policy_state.is_not(None))
                    .order_by(ChatSessionModel.started_at.desc())
                    .limit(1)
                )
                existing = (await db.execute(resumable_stmt)).scalar_one_or_none()

                if existing:
                    db_session = existing
                    db_session.status = SessionStatus.IN_PROGRESS  # reset lifecycle
                    await db.commit()

                    # Suppress the speculative greeting if the prior session
                    # already produced model output. Otherwise the LLM would
                    # generate a brand-new greeting using the existing
                    # transcript as context, and the student perceives it as
                    # "Milo started over from scratch".
                    has_model_history = (
                        await db.execute(
                            select(ChatMessage.id)
                            .where(ChatMessage.session_id == existing.id)
                            .where(ChatMessage.role == "model")
                            .limit(1)
                        )
                    ).first() is not None
                    self._needs_greeting = not has_model_history

                    logger.info(
                        "Session '%s': resuming prior session for user=%s activity=%s "
                        "(prior status=%s, needs_greeting=%s).",
                        existing.id, self._user_id, activity_uuid,
                        existing.status, self._needs_greeting,
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
            should_evaluate = False
            async with get_db_session() as db:
                session = await db.get(ChatSessionModel, self._session_id_uuid)
                if session is None:
                    return

                # Always snapshot policy_state so a future resume rehydrates
                # cross-turn state (hint ladder, recovery, etc.).
                session.policy_state = PolicyStateSnapshot.from_session(self).serialize()

                # Evaluation only fires for LLM-finalized sessions. An
                # unfinalized session — whether greeting-only or full of
                # back-and-forth that never reached natural closure — is
                # still "in progress" from the student's POV and from the
                # teacher's analytics POV. Status stays IN_PROGRESS until
                # the LLM emits the closure sentinel.
                should_evaluate = session.finalized_at is not None
                if should_evaluate:
                    session.status = SessionStatus.PENDING_EVALUATION
                await db.commit()

                if should_evaluate:
                    metric_row = await db.get(SessionMetric, self._session_id_uuid)
                    if metric_row is None:
                        metric_row = SessionMetric(session_id=self._session_id_uuid)
                        db.add(metric_row)
                    metric_row.policy_metrics = self._metrics.snapshot()
                    await db.commit()

                # Drop an "unfinished activity" notification only if the
                # student actually engaged AND the LLM never closed.
                if session.finalized_at is None:
                    user_msg = (
                        await db.execute(
                            select(ChatMessage.id)
                            .where(ChatMessage.session_id == self._session_id_uuid)
                            .where(ChatMessage.role == "user")
                            .limit(1)
                        )
                    ).first()
                    if user_msg is not None:
                        activity = await db.get(ReflectionActivity, session.activity_id)
                        if activity is not None:
                            await notify_unfinished_activity(
                                db,
                                user_id=self._user_id,
                                activity_id=activity.id,
                                activity_title=activity.title,
                            )
                            await db.commit()

            if not should_evaluate:
                return

            if not _AUTO_EVALUATE_ON_CHAT_CLOSE:
                logger.info(
                    "Session '%s': AUTO_EVALUATE_ON_CHAT_CLOSE=false, skipping evaluation.",
                    self._session_id,
                )
                return

            # Non-blocking background task scheduling
            await queue_evaluation(self._session_id_uuid, self._agent)
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
            if self._should_close:
                # The previous turn ended with the LLM's closure sentinel.
                # Hold the WebSocket open for a moment so the client receives
                # the session_complete frame; then exit and let _wrap_up_session
                # run via the finally block.
                logger.info(
                    "Session '%s': loop exiting after LLM closure.",
                    self._session_id,
                )
                break

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
            turn_count = 0
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

            # Step 3: stream the LLM response.
            # Closure-eligible turns are buffered fully so we can suppress
            # the entire closing message if the sentinel appears — the
            # LLM's closing text has historically been unreliable (it
            # sometimes emits a Socratic question alongside the sentinel)
            # and the UI shows a deterministic "activity finished"
            # announcement instead. Non-eligible turns stream live with a
            # small tail buffer so a stray sentinel can be stripped from
            # the final chunk.
            closure_eligible = decision is not None and decision.closure_eligible
            accumulated: List[str] = []
            sentinel_detected = False

            async with get_db_session() as db:
                stream = self._agent.process_session_stream(
                    db, self._user_id, self._session_id, user_text,
                    self._context_description, self._activity_id,
                    prompt_directives=prompt_directives,
                )

                if closure_eligible:
                    async for chunk in stream:
                        accumulated.append(chunk)
                    full_text = "".join(accumulated)
                    sentinel_detected = CLOSURE_SENTINEL in full_text
                    if not sentinel_detected:
                        if not await self._send_json({"type": "chunk", "text": full_text}):
                            logger.info(
                                "Session '%s': client dropped mid-stream - halting.",
                                self._session_id,
                            )
                            return
                    # When sentinel_detected, deliberately send NO chunks —
                    # the session_complete frame drives the UI.
                else:
                    tail_buffer = ""
                    tail_size = len(CLOSURE_SENTINEL) + 8  # slack for whitespace/newlines
                    async for chunk in stream:
                        tail_buffer += chunk
                        if len(tail_buffer) > tail_size:
                            head = tail_buffer[:-tail_size]
                            tail_buffer = tail_buffer[-tail_size:]
                            accumulated.append(head)
                            if not await self._send_json({"type": "chunk", "text": head}):
                                logger.info(
                                    "Session '%s': client dropped mid-stream - halting.",
                                    self._session_id,
                                )
                                return
                    sentinel_detected = CLOSURE_SENTINEL in tail_buffer
                    if sentinel_detected:
                        tail_buffer = tail_buffer.replace(CLOSURE_SENTINEL, "").rstrip()
                    if tail_buffer:
                        accumulated.append(tail_buffer)
                        if not await self._send_json({"type": "chunk", "text": tail_buffer}):
                            logger.info(
                                "Session '%s': client dropped mid-stream - halting.",
                                self._session_id,
                            )
                            return

            # Step 4: output interception.
            # Skip the interceptors when the LLM emitted the closure sentinel —
            # the closing message is intentional and must NOT have a Socratic
            # question appended by the rhetorical/direct-answer interceptors.
            # Otherwise the student sees a goodbye followed by another question
            # and the turn reads as continuation, not closure. State update +
            # `done` frame still run normally below.
            if decision is not None:
                was_intercepted = False
                if not sentinel_detected:
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

            # Step 7: honor the LLM's closure sentinel, if any.
            if sentinel_detected:
                if turn_count >= CLOSURE_MIN_TURNS:
                    if self._session_id_uuid is not None:
                        try:
                            async with get_db_session() as db:
                                session_row = await db.get(
                                    ChatSessionModel, self._session_id_uuid
                                )
                                if session_row is not None and session_row.finalized_at is None:
                                    session_row.finalized_at = datetime.now(timezone.utc)

                                latest_msg = (
                                    await db.execute(
                                        select(ChatMessage)
                                        .where(ChatMessage.session_id == self._session_id_uuid)
                                        .where(ChatMessage.role == "model")
                                        .order_by(ChatMessage.created_at.desc())
                                        .limit(1)
                                    )
                                ).scalar_one_or_none()

                                if latest_msg is not None:
                                    if closure_eligible:
                                        # The closing turn was suppressed
                                        # from the client; drop the row
                                        # from the transcript too so
                                        # reviewers don't see the LLM's
                                        # (occasionally noisy) closure
                                        # text.
                                        await db.delete(latest_msg)
                                    elif CLOSURE_SENTINEL in latest_msg.content:
                                        latest_msg.content = (
                                            latest_msg.content.replace(CLOSURE_SENTINEL, "").rstrip()
                                        )
                                await db.commit()
                        except Exception:
                            logger.exception(
                                "Session '%s': failed to persist finalized_at.",
                                self._session_id,
                            )
                    await self._send_json({"type": "session_complete"})
                    self._should_close = True
                    logger.info(
                        "Session '%s': finalized by LLM closure sentinel.",
                        self._session_id,
                    )
                else:
                    logger.warning(
                        "Session '%s': closure sentinel ignored at turn %d (< %d).",
                        self._session_id, turn_count, CLOSURE_MIN_TURNS,
                    )

        except PermissionError:
            await self._send_error("No tenes permiso para acceder a esta conversacion.")
        except RuntimeError as exc:
            logger.error("Session '%s': agent error - %s", self._session_id, exc)
            await self._send_error(str(exc))
        except asyncio.CancelledError:
            logger.info("Session '%s': turn cancelled mid-flight.", self._session_id)
            raise
