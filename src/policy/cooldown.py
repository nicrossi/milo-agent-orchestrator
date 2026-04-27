"""
Meta-feedback cooldown — Aleven's help-seeking work shows that intervening on
>75% of actions is counterproductive. We classify rules as essential or
non-essential and rate-limit non-essential firings to ≤ 1 per 2 turns.

Essential rules always fire (they enforce hard guardrails or route the turn):
  - NoDirectAnswersRule
  - ElicitAttemptRule
  - HintLadderRule

Non-essential rules are cosmetic / meta and get rate-limited:
  - ToneByConfidenceRule

Each rule subclass declares its essentiality via a class-level `essential`
attribute on BaseRule.
"""
from __future__ import annotations

_COOLDOWN_TURNS = 2


class MetaFeedbackCooldown:
    """Stateless wrapper around `turns_since_meta_feedback`.

    Use:
        cd = MetaFeedbackCooldown(ctx.turns_since_meta_feedback)
        if cd.allows_intervention():
            # non-essential rule may fire
            ...

    After all rules run, call `compute_next(any_non_essential_fired)` to get
    the value for the next turn.
    """

    def __init__(self, turns_since_meta_feedback: int) -> None:
        self._counter = turns_since_meta_feedback

    def allows_intervention(self) -> bool:
        """True iff a non-essential rule may fire this turn."""
        return self._counter >= _COOLDOWN_TURNS

    def compute_next(self, any_non_essential_fired: bool) -> int:
        """Returns the new turns_since_meta_feedback for the next turn.

        Resets to 0 when a non-essential rule fired; otherwise increments by 1.
        """
        if any_non_essential_fired:
            return 0
        # Cap at a high value to avoid unbounded growth.
        return min(self._counter + 1, 999)
