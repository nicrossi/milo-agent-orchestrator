"""Tests for src/policy/hint_ladder.py — state machine purity, advancement,
reset, and bottom-out gating."""
from src.policy.hint_ladder import next_step
from src.policy.types import HintLadderState, RecoveryState, Scores, UserSignals


def _high_struggle() -> Scores:
    return Scores(struggle=0.8)


def _low_struggle() -> Scores:
    return Scores(struggle=0.1)


def _signals(attempt: bool = True) -> UserSignals:
    return UserSignals(attempt_present=attempt)


# --- advancement ---

def test_advances_from_process_to_strategic_on_high_struggle():
    s, t, low = next_step(
        HintLadderState.PROCESS_FEEDBACK, 0, 0,
        _high_struggle(), _signals(True), RecoveryState.NORMAL,
    )
    assert s == HintLadderState.STRATEGIC_HINT
    assert t == 0   # reset on transition
    assert low == 0


def test_advances_strategic_to_focused():
    s, _, _ = next_step(
        HintLadderState.STRATEGIC_HINT, 1, 0,
        _high_struggle(), _signals(True), RecoveryState.NORMAL,
    )
    assert s == HintLadderState.FOCUSED_HINT


def test_does_not_advance_without_attempt():
    # Spec: advance only when attempt_present.
    s, t, _ = next_step(
        HintLadderState.PROCESS_FEEDBACK, 0, 0,
        _high_struggle(), _signals(False), RecoveryState.NORMAL,
    )
    assert s == HintLadderState.PROCESS_FEEDBACK
    assert t == 1


def test_does_not_advance_on_low_struggle():
    s, t, low = next_step(
        HintLadderState.PROCESS_FEEDBACK, 0, 0,
        _low_struggle(), _signals(True), RecoveryState.NORMAL,
    )
    assert s == HintLadderState.PROCESS_FEEDBACK
    assert low == 1  # streak counter rises


# --- bottom-out gating ---

def test_focused_does_not_jump_to_bottom_before_min_turns():
    # First turn at FOCUSED_HINT: should stay (not enough time at this rung).
    s, t, _ = next_step(
        HintLadderState.FOCUSED_HINT, 0, 0,
        _high_struggle(), _signals(True), RecoveryState.NORMAL,
    )
    assert s == HintLadderState.FOCUSED_HINT
    assert t == 1


def test_focused_advances_to_bottom_out_after_three_turns():
    # turns_in_state=3 means we've spent 3 full turns at FOCUSED → bottom-out OK.
    s, _, _ = next_step(
        HintLadderState.FOCUSED_HINT, 3, 0,
        _high_struggle(), _signals(True), RecoveryState.NORMAL,
    )
    assert s == HintLadderState.BOTTOM_OUT


def test_bottom_out_saturates():
    s, _, _ = next_step(
        HintLadderState.BOTTOM_OUT, 1, 0,
        _high_struggle(), _signals(True), RecoveryState.NORMAL,
    )
    assert s == HintLadderState.BOTTOM_OUT


def test_full_walk_never_reaches_bottom_before_turn_six():
    """Acceptance: 5 turns of struggle=0.8+attempt cannot reach BOTTOM_OUT."""
    state = HintLadderState.PROCESS_FEEDBACK
    turns_in = 0
    low = 0
    visited = []
    for _ in range(5):
        state, turns_in, low = next_step(
            state, turns_in, low,
            _high_struggle(), _signals(True), RecoveryState.NORMAL,
        )
        visited.append(state)
    assert HintLadderState.BOTTOM_OUT not in visited


def test_full_walk_reaches_bottom_after_six_turns():
    state = HintLadderState.PROCESS_FEEDBACK
    turns_in = 0
    low = 0
    for _ in range(6):
        state, turns_in, low = next_step(
            state, turns_in, low,
            _high_struggle(), _signals(True), RecoveryState.NORMAL,
        )
    assert state == HintLadderState.BOTTOM_OUT


# --- reset on sustained low struggle ---

def test_reset_after_two_consecutive_low_struggle_turns():
    state = HintLadderState.FOCUSED_HINT
    turns_in = 1
    low = 0
    # Turn 1: low struggle, low=1
    state, turns_in, low = next_step(
        state, turns_in, low, _low_struggle(), _signals(True), RecoveryState.NORMAL,
    )
    assert state == HintLadderState.FOCUSED_HINT
    assert low == 1
    # Turn 2: low struggle again → reset to PROCESS_FEEDBACK
    state, turns_in, low = next_step(
        state, turns_in, low, _low_struggle(), _signals(True), RecoveryState.NORMAL,
    )
    assert state == HintLadderState.PROCESS_FEEDBACK


def test_low_streak_resets_on_high_struggle():
    state, turns_in, low = next_step(
        HintLadderState.STRATEGIC_HINT, 1, 1,
        _high_struggle(), _signals(True), RecoveryState.NORMAL,
    )
    assert low == 0


# --- recovery freeze ---

def test_recovery_freezes_ladder_state():
    s, t, _ = next_step(
        HintLadderState.STRATEGIC_HINT, 0, 0,
        _high_struggle(), _signals(True), RecoveryState.STABILIZE,
    )
    assert s == HintLadderState.STRATEGIC_HINT
    assert t == 1


def test_recovery_blocks_bottom_out_advancement():
    s, _, _ = next_step(
        HintLadderState.FOCUSED_HINT, 5, 0,    # eligible for bottom out
        _high_struggle(), _signals(True), RecoveryState.STABILIZE,
    )
    # Recovery override — stays at FOCUSED.
    assert s == HintLadderState.FOCUSED_HINT
