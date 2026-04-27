"""Tests for src/policy/recovery.py — STABILIZE entry, exit, and capping."""
from src.policy.recovery import next_state
from src.policy.types import RecoveryState, Scores, UserSignals


def _affect_window(n: int) -> list[UserSignals]:
    """Window of n turns with high confusion/affect."""
    return [UserSignals(confusion=0.8, hedging=0.5) for _ in range(n)]


# --- entry ---

def test_does_not_enter_with_low_affect():
    s, t = next_state(
        current=RecoveryState.NORMAL,
        turns_in_recovery=0,
        scores=Scores(affect_load=0.3, struggle=0.2),
        user_signals=UserSignals(confusion=0.1),
        signals_window=[],
    )
    assert s == RecoveryState.NORMAL
    assert t == 0


def test_does_not_enter_without_window_history():
    # Single-turn high affect, no prior history → not enough signal.
    s, _ = next_state(
        current=RecoveryState.NORMAL,
        turns_in_recovery=0,
        scores=Scores(affect_load=0.7),
        user_signals=UserSignals(confusion=0.7, hedging=0.3),
        signals_window=[],
    )
    assert s == RecoveryState.NORMAL


def test_enters_when_all_conditions_met():
    s, t = next_state(
        current=RecoveryState.NORMAL,
        turns_in_recovery=0,
        scores=Scores(affect_load=0.7),
        user_signals=UserSignals(confusion=0.7, hedging=0.3),
        signals_window=_affect_window(2),
    )
    assert s == RecoveryState.STABILIZE
    assert t == 1


def test_does_not_enter_with_low_confusion_even_if_affect_high():
    s, _ = next_state(
        current=RecoveryState.NORMAL,
        turns_in_recovery=0,
        scores=Scores(affect_load=0.7),
        user_signals=UserSignals(confusion=0.1, hedging=0.9),
        signals_window=_affect_window(2),
    )
    assert s == RecoveryState.NORMAL


# --- stay / exit ---

def test_stays_in_stabilize_while_affect_remains_high():
    s, t = next_state(
        current=RecoveryState.STABILIZE,
        turns_in_recovery=1,
        scores=Scores(affect_load=0.6),
        user_signals=UserSignals(confusion=0.5, hedging=0.3),
        signals_window=_affect_window(3),
    )
    assert s == RecoveryState.STABILIZE
    assert t == 2


def test_exits_when_affect_drops():
    s, t = next_state(
        current=RecoveryState.STABILIZE,
        turns_in_recovery=2,
        scores=Scores(affect_load=0.1),
        user_signals=UserSignals(confusion=0.05),
        signals_window=_affect_window(3),
    )
    assert s == RecoveryState.NORMAL
    assert t == 0


def test_caps_at_max_recovery_turns():
    # turns_in_recovery=3 → 4th turn hits cap → exit.
    s, t = next_state(
        current=RecoveryState.STABILIZE,
        turns_in_recovery=3,
        scores=Scores(affect_load=0.9),
        user_signals=UserSignals(confusion=0.9),
        signals_window=_affect_window(5),
    )
    assert s == RecoveryState.NORMAL
    assert t == 0
