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
  - procedural_unblocks: count of turns where ProceduralUnblockRule fired.
  - pre_unblock_stuckness_run_max: longest run of procedural_request=True
    turns observed BEFORE the rule fired (or before session end if it
    never fired). Calibration target ≤ 2; large values mean the threshold
    or cooldown is too conservative.
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
        # Procedural-roadblock instrumentation.
        self.procedural_unblocks = 0
        self.pre_unblock_stuckness_run_max = 0
        self._current_stuckness_run = 0

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

        # Procedural-roadblock tracking.
        block_score = (
            decision.scores.procedural_block if decision.scores else 0.0
        )
        if "procedural_unblock" in decision.applied_rules:
            self.procedural_unblocks += 1
            # Capture run length up to (and including) the firing turn, then reset.
            self.pre_unblock_stuckness_run_max = max(
                self.pre_unblock_stuckness_run_max,
                self._current_stuckness_run + 1,
            )
            self._current_stuckness_run = 0
        elif block_score > 0.0:
            self._current_stuckness_run += 1
            self.pre_unblock_stuckness_run_max = max(
                self.pre_unblock_stuckness_run_max,
                self._current_stuckness_run,
            )
        else:
            self._current_stuckness_run = 0

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
            "procedural_unblocks": self.procedural_unblocks,
            "pre_unblock_stuckness_run_max": self.pre_unblock_stuckness_run_max,
        }
