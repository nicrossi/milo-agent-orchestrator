"""
Policy state snapshot — serializable view of every cross-turn ChatSession
field that needs to survive a WebSocket reconnect.

Stored as JSON on the chat_sessions row. Versioned so future schema changes
can migrate older snapshots without crashing live sessions.

Round-trip:
  snapshot = PolicyStateSnapshot.from_session(chat_session)
  blob = snapshot.serialize()                       # → dict (JSON-safe)
  ...later...
  snapshot = PolicyStateSnapshot.deserialize(blob)  # → typed model
  snapshot.apply_to(chat_session)                   # rehydrate fields

If `deserialize` encounters a malformed blob or a version it can't migrate,
it returns None instead of raising. The caller treats that as "no snapshot"
and starts fresh — better to lose state than to crash the session.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from src.policy.types import (
    FSMState,
    HintLadderState,
    RecoveryState,
    UserSignals,
)

logger = logging.getLogger("milo-orchestrator.policy.persistence")

_CURRENT_VERSION = 1


class PolicyStateSnapshot(BaseModel):
    """All cross-turn state that needs to survive a reconnect.

    Field names match the ChatSession in-memory attributes (without the
    leading underscore) so apply_to() can use setattr-by-name.
    """

    version: int = _CURRENT_VERSION

    # FSM
    fsm_state: FSMState = FSMState.PLANNING
    recent_question_ids: list[str] = Field(default_factory=list)

    # Phase 4 ladder + recovery + cooldown
    hint_state: HintLadderState = HintLadderState.PROCESS_FEEDBACK
    turns_in_hint_state: int = 0
    consecutive_low_struggle_turns: int = 0
    recovery_state: RecoveryState = RecoveryState.NORMAL
    turns_in_recovery: int = 0
    turns_since_meta_feedback: int = 99

    # Phase 1 signal windows
    signals_window: list[UserSignals] = Field(default_factory=list)
    length_window: list[int] = Field(default_factory=list)
    latency_window: list[float] = Field(default_factory=list)
    last_milo_response_ts: Optional[float] = None

    # ----- snapshot construction -----

    @classmethod
    def from_session(cls, session: Any) -> "PolicyStateSnapshot":
        """Capture the current ChatSession state into a snapshot."""
        return cls(
            fsm_state=session._fsm_state,
            recent_question_ids=list(session._recent_question_ids),
            hint_state=session._hint_state,
            turns_in_hint_state=session._turns_in_hint_state,
            consecutive_low_struggle_turns=session._consecutive_low_struggle_turns,
            recovery_state=session._recovery_state,
            turns_in_recovery=session._turns_in_recovery,
            turns_since_meta_feedback=session._turns_since_meta_feedback,
            signals_window=list(session._signals_window),
            length_window=list(session._length_window),
            latency_window=list(session._latency_window),
            last_milo_response_ts=session._last_milo_response_ts,
        )

    # ----- serialization -----

    def serialize(self) -> dict:
        return self.model_dump(mode="json")

    @classmethod
    def deserialize(cls, blob: Optional[dict]) -> Optional["PolicyStateSnapshot"]:
        """Robust load. Returns None on any failure (caller treats as fresh)."""
        if not blob:
            return None
        try:
            version = int(blob.get("version", 0))
        except (TypeError, ValueError):
            logger.warning("PolicyStateSnapshot deserialize: invalid version field; resetting.")
            return None

        if version > _CURRENT_VERSION:
            logger.warning(
                "PolicyStateSnapshot version %s newer than this server (%s); resetting.",
                version, _CURRENT_VERSION,
            )
            return None

        # Future migrations would slot in here. For now any version <=1 attempts
        # validation against the current schema directly.
        try:
            return cls.model_validate(blob)
        except ValidationError as exc:
            logger.warning("PolicyStateSnapshot deserialize failed: %s", exc)
            return None

    # ----- rehydration -----

    def apply_to(self, session: Any) -> None:
        """Restore the snapshot fields onto a ChatSession in-place."""
        session._fsm_state = self.fsm_state
        session._recent_question_ids = list(self.recent_question_ids)
        session._hint_state = self.hint_state
        session._turns_in_hint_state = self.turns_in_hint_state
        session._consecutive_low_struggle_turns = self.consecutive_low_struggle_turns
        session._recovery_state = self.recovery_state
        session._turns_in_recovery = self.turns_in_recovery
        session._turns_since_meta_feedback = self.turns_since_meta_feedback
        session._signals_window = list(self.signals_window)
        session._length_window = list(self.length_window)
        session._latency_window = list(self.latency_window)
        session._last_milo_response_ts = self.last_milo_response_ts
