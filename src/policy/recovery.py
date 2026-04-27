"""
Confusion recovery — D'Mello & Graesser observed that high cognitive load /
disequilibrium needs validation + narrowed-choice support before more
metacognitive prompting is helpful.

This module decides whether to enter / stay in / exit the STABILIZE micro-state.
Entry requires multiple converging signals so we don't trip on a single
hedging turn:

  Entry:  affect_load > _AFFECT_HIGH  AND  confusion > _CONFUSION_HIGH
          AND ≥ _MIN_AFFECT_TURNS_IN_WINDOW recent turns with affect_load > 0.4
          AND not already in STABILIZE.

  Exit:   turns_in_recovery >= _MAX_RECOVERY_TURNS   (cap)
          OR affect_load < _AFFECT_LOW AND confusion < _CONFUSION_LOW
             (learner has stabilized).

While in STABILIZE, the engine:
  - Forces the question selector to the RECOVERY_STABILIZE family.
  - Pauses FSM transitions (current_state passes through unchanged).
  - Holds the hint ladder at its current rung (does not escalate to BOTTOM_OUT).
"""
from __future__ import annotations

from typing import Sequence

from src.policy.types import RecoveryState, Scores, UserSignals

# Entry threshold matches _AFFECT_TURN_THRESHOLD so that "the current turn
# is high-affect" is consistent with "this turn would have counted as a high-
# affect prior turn from a future turn's perspective". The previous 0.5
# entry threshold was stricter than the per-turn window classifier, which
# left a dead-zone where 3 sustained moderate-affect turns failed to enter.
_AFFECT_HIGH = 0.4
_AFFECT_LOW = 0.3
_CONFUSION_HIGH = 0.4
_CONFUSION_LOW = 0.2
_MIN_AFFECT_TURNS_IN_WINDOW = 2          # need ≥ 2 prior high-affect turns
_AFFECT_TURN_THRESHOLD = 0.4             # what counts as "high-affect" for the window
_MAX_RECOVERY_TURNS = 4


def _high_affect_count(window: Sequence[UserSignals]) -> int:
    return sum(
        1 for s in window
        if (0.6 * s.confusion + 0.4 * s.hedging) > _AFFECT_TURN_THRESHOLD
    )


def next_state(
    current: RecoveryState,
    turns_in_recovery: int,
    scores: Scores,
    user_signals: UserSignals,
    signals_window: Sequence[UserSignals],
) -> tuple[RecoveryState, int]:
    """Return (next_recovery_state, new_turns_in_recovery)."""
    if current == RecoveryState.STABILIZE:
        # Exit conditions
        if turns_in_recovery + 1 >= _MAX_RECOVERY_TURNS:
            return RecoveryState.NORMAL, 0
        if scores.affect_load < _AFFECT_LOW and user_signals.confusion < _CONFUSION_LOW:
            return RecoveryState.NORMAL, 0
        return RecoveryState.STABILIZE, turns_in_recovery + 1

    # Currently NORMAL — should we enter?
    if scores.affect_load <= _AFFECT_HIGH:
        return RecoveryState.NORMAL, 0
    if user_signals.confusion <= _CONFUSION_HIGH:
        return RecoveryState.NORMAL, 0
    if _high_affect_count(signals_window) < _MIN_AFFECT_TURNS_IN_WINDOW:
        return RecoveryState.NORMAL, 0

    return RecoveryState.STABILIZE, 1
