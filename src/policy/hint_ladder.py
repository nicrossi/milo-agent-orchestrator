"""
Hint ladder — graduated escalation per the Koedinger & Aleven assistance
dilemma. The learner only reaches a near-answer (BOTTOM_OUT) after sustained
unproductive struggle, never as a first move.

Rungs (lowest to highest):
  PROCESS_FEEDBACK  → process cue, no specifics
  STRATEGIC_HINT    → directional hint, no concrete sub-step
  FOCUSED_HINT      → name the concept / sub-goal
  BOTTOM_OUT        → worked sub-step (last resort, gated)

State transitions (pure function of current state + signals + counters):
  - Advance one rung when struggle > _STRUGGLE_HIGH AND attempt_present.
  - Reset to PROCESS_FEEDBACK after _LOW_STRUGGLE_RESET turns of struggle < _STRUGGLE_LOW.
  - BOTTOM_OUT is reachable only after ≥ _MIN_TURNS_AT_FOCUSED at FOCUSED_HINT.
  - When in recovery (recovery_state == STABILIZE), the ladder is held at its
    current state (do not advance into BOTTOM_OUT while the learner is overwhelmed).
"""
from __future__ import annotations

from src.policy.types import HintLadderState, RecoveryState, Scores, UserSignals

_STRUGGLE_HIGH = 0.6
_STRUGGLE_LOW = 0.3
_LOW_STRUGGLE_RESET = 2
_MIN_TURNS_AT_FOCUSED = 3


_LADDER_ORDER = [
    HintLadderState.PROCESS_FEEDBACK,
    HintLadderState.STRATEGIC_HINT,
    HintLadderState.FOCUSED_HINT,
    HintLadderState.BOTTOM_OUT,
]


def next_step(
    current: HintLadderState,
    turns_in_state: int,
    consecutive_low_struggle: int,
    scores: Scores,
    user_signals: UserSignals,
    recovery_state: RecoveryState,
) -> tuple[HintLadderState, int, int]:
    """Compute next ladder state + updated counters.

    Returns (next_state, new_turns_in_state, new_consecutive_low_struggle).
    """
    # While in recovery, freeze the ladder. Counters age but don't escalate.
    if recovery_state == RecoveryState.STABILIZE:
        if scores.struggle < _STRUGGLE_LOW:
            new_low = consecutive_low_struggle + 1
        else:
            new_low = 0
        return current, turns_in_state + 1, new_low

    # Reset path: 2 consecutive low-struggle turns → back to PROCESS_FEEDBACK.
    if scores.struggle < _STRUGGLE_LOW:
        new_low = consecutive_low_struggle + 1
        if new_low >= _LOW_STRUGGLE_RESET and current != HintLadderState.PROCESS_FEEDBACK:
            return HintLadderState.PROCESS_FEEDBACK, 0, new_low
        return current, turns_in_state + 1, new_low

    # Reset the low-streak counter on any non-low turn.
    new_low = 0

    # Advance only on real productive struggle (high struggle + an attempt).
    should_advance = (
        scores.struggle > _STRUGGLE_HIGH and user_signals.attempt_present
    )
    if not should_advance:
        return current, turns_in_state + 1, new_low

    # Compute the next rung — but gate BOTTOM_OUT.
    idx = _LADDER_ORDER.index(current)
    if current == HintLadderState.FOCUSED_HINT:
        # Bottom-out only after _MIN_TURNS_AT_FOCUSED full turns spent here
        # (turns_in_state already counts elapsed turns at this rung).
        if turns_in_state >= _MIN_TURNS_AT_FOCUSED:
            return HintLadderState.BOTTOM_OUT, 0, new_low
        return HintLadderState.FOCUSED_HINT, turns_in_state + 1, new_low

    if current == HintLadderState.BOTTOM_OUT:
        # Saturated — stay.
        return HintLadderState.BOTTOM_OUT, turns_in_state + 1, new_low

    next_rung = _LADDER_ORDER[idx + 1]
    return next_rung, 0, new_low
