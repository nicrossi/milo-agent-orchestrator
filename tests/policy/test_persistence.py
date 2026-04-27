"""Tests for src/policy/persistence.py — round-trip + version + robustness."""
from src.policy.persistence import PolicyStateSnapshot, _CURRENT_VERSION
from src.policy.types import (
    FSMState,
    HintLadderState,
    RecoveryState,
    UserSignals,
)


class _FakeSession:
    """Stand-in for ChatSession that exposes only the fields persistence needs."""

    def __init__(self):
        self._fsm_state = FSMState.MONITORING
        self._recent_question_ids = ["goal_01", "check_01"]
        self._hint_state = HintLadderState.STRATEGIC_HINT
        self._turns_in_hint_state = 1
        self._consecutive_low_struggle_turns = 0
        self._recovery_state = RecoveryState.NORMAL
        self._turns_in_recovery = 0
        self._turns_since_meta_feedback = 3
        self._signals_window = [UserSignals(hedging=0.5)]
        self._length_window = [4, 7]
        self._latency_window = [3.2, 4.1]
        self._last_milo_response_ts = 1700000000.0


# --- snapshot construction ---

def test_from_session_captures_all_fields():
    s = _FakeSession()
    snap = PolicyStateSnapshot.from_session(s)
    assert snap.fsm_state == FSMState.MONITORING
    assert snap.recent_question_ids == ["goal_01", "check_01"]
    assert snap.hint_state == HintLadderState.STRATEGIC_HINT
    assert snap.turns_in_hint_state == 1
    assert snap.recovery_state == RecoveryState.NORMAL
    assert snap.turns_since_meta_feedback == 3
    assert len(snap.signals_window) == 1
    assert snap.length_window == [4, 7]


def test_default_snapshot_has_current_version():
    snap = PolicyStateSnapshot()
    assert snap.version == _CURRENT_VERSION


# --- serialization round-trip ---

def test_serialize_then_deserialize_preserves_state():
    s = _FakeSession()
    snap = PolicyStateSnapshot.from_session(s)
    blob = snap.serialize()
    assert isinstance(blob, dict)
    assert blob["version"] == _CURRENT_VERSION

    restored = PolicyStateSnapshot.deserialize(blob)
    assert restored is not None
    assert restored.fsm_state == snap.fsm_state
    assert restored.recent_question_ids == snap.recent_question_ids
    assert restored.hint_state == snap.hint_state
    assert restored.turns_since_meta_feedback == snap.turns_since_meta_feedback


def test_apply_to_rehydrates_session():
    s = _FakeSession()
    blob = PolicyStateSnapshot.from_session(s).serialize()

    fresh = _FakeSession()
    fresh._fsm_state = FSMState.PLANNING
    fresh._recent_question_ids = []
    fresh._hint_state = HintLadderState.PROCESS_FEEDBACK

    snap = PolicyStateSnapshot.deserialize(blob)
    snap.apply_to(fresh)

    assert fresh._fsm_state == FSMState.MONITORING
    assert fresh._recent_question_ids == ["goal_01", "check_01"]
    assert fresh._hint_state == HintLadderState.STRATEGIC_HINT


# --- robustness ---

def test_deserialize_returns_none_on_empty():
    assert PolicyStateSnapshot.deserialize(None) is None
    assert PolicyStateSnapshot.deserialize({}) is None


def test_deserialize_returns_none_on_invalid_version():
    assert PolicyStateSnapshot.deserialize({"version": "abc"}) is None


def test_deserialize_returns_none_on_future_version():
    blob = {"version": _CURRENT_VERSION + 1, "fsm_state": "PLANNING"}
    assert PolicyStateSnapshot.deserialize(blob) is None


def test_deserialize_returns_none_on_malformed_blob():
    blob = {"version": 1, "fsm_state": "NOT_A_REAL_STATE"}
    assert PolicyStateSnapshot.deserialize(blob) is None


def test_deserialize_accepts_minimal_valid_blob():
    # Only version is required; everything else falls back to defaults.
    blob = {"version": 1}
    snap = PolicyStateSnapshot.deserialize(blob)
    assert snap is not None
    assert snap.fsm_state == FSMState.PLANNING
    assert snap.recent_question_ids == []
