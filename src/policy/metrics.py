"""
Per-session metrics collector — observability scaffolding for the thesis.

The collector lives on a ChatSession and accumulates counters across turns.
At session end, snapshot() returns a JSON-serializable view that is written
to SessionMetric.policy_metrics.

Tracked metrics:
  - total_turns
  - direct_answer_leakage_rate: fraction of turns where the output interceptor
    had to correct a direct-answer leak. The thesis target is 0.0.
  - hint_distribution: histogram of HintLadderState values per turn. Useful
    for proving bottom-out is rare relative to process feedback.
  - over_intervention_rate: fraction of turns with > 1 rule fired. Aleven's
    "intervening on >75% of actions is annoying" → keep this < 0.25.
  - rule_firing_counts: per-rule firing counts for traceability.
  - calibration_gap_proxy: fraction of turns with high miscalibration score.
  - recovery_entries: count of NORMAL → STABILIZE transitions.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from src.policy.types import HintLadderState, PolicyDecision, RecoveryState


class MetricsCollector:
    def __init__(self) -> None:
        self.total_turns = 0
        self.direct_answer_leakages = 0
        self.hint_distribution: dict[str, int] = {
            s.value: 0 for s in HintLadderState
        }
        self.rule_firing_counts: dict[str, int] = defaultdict(int)
        self.over_intervention_turns = 0  # turns with > 1 rule fired
        self.miscalibration_high_turns = 0
        self.recovery_entries = 0
        self._prev_recovery_state: RecoveryState = RecoveryState.NORMAL

    # ---- per-turn instrumentation ----

    def record_decision(self, decision: PolicyDecision) -> None:
        """Capture per-turn metrics from the engine's decision."""
        self.total_turns += 1
        self.hint_distribution[decision.next_hint_state.value] += 1

        for rule in decision.applied_rules:
            self.rule_firing_counts[rule] += 1
        if len(decision.applied_rules) > 1:
            self.over_intervention_turns += 1

        if decision.scores and decision.scores.miscalibration > 0.5:
            self.miscalibration_high_turns += 1

        # Count NORMAL → STABILIZE transitions.
        if (
            decision.next_recovery_state == RecoveryState.STABILIZE
            and self._prev_recovery_state == RecoveryState.NORMAL
        ):
            self.recovery_entries += 1
        self._prev_recovery_state = decision.next_recovery_state

    def record_interceptor_correction(self, was_modified: bool) -> None:
        """Called after engine.check_output. `was_modified` true → leak caught."""
        if was_modified:
            self.direct_answer_leakages += 1

    # ---- snapshot ----

    def snapshot(self) -> dict:
        denom = max(self.total_turns, 1)
        return {
            "total_turns": self.total_turns,
            "direct_answer_leakage_rate": self.direct_answer_leakages / denom,
            "hint_distribution": dict(self.hint_distribution),
            "over_intervention_rate": self.over_intervention_turns / denom,
            "rule_firing_counts": dict(self.rule_firing_counts),
            "calibration_gap_proxy": self.miscalibration_high_turns / denom,
            "recovery_entries": self.recovery_entries,
        }
