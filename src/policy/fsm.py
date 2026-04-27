"""
Phase 6 — score-driven FSM transitions.

Transition logic (no longer reads UserSignals.confidence):

  PLANNING → MONITORING
    when turn_count >= PLANNING_TO_MONITORING_TURN.

  MONITORING → EVALUATION
    when turn_count >= MONITORING_TO_EVALUATION_TURN
      OR (turn_count >= MIN_TURNS_BEFORE_ACCEL
          AND attempt_present
          AND miscalibration < CLEAR_THRESHOLD
          AND struggle < CLEAR_THRESHOLD
          AND affect_load < CLEAR_THRESHOLD)
        — accelerates when the learner has spent some turns in MONITORING
          showing real engagement + clear AND low-affect signals. The
          affect_load guard prevents falsely advancing a confused learner
          (high affect_load but low struggle/miscalibration).

  EVALUATION → PLANNING
    when turn_count >= EVALUATION_RESET_TURN
      OR struggle > OVERLOAD_THRESHOLD
      OR affect_load > OVERLOAD_THRESHOLD
        — resets when the learner is overwhelmed and needs to re-plan.

The function is pure: same input → same output, no side effects. Recovery
(STABILIZE) is handled in PolicyEngine.evaluate(); this module is agnostic.
"""
from src.policy.types import FSMState, PolicyContext

# Turn-count thresholds — unchanged from earlier phases.
PLANNING_TO_MONITORING_TURN = 2
MONITORING_TO_EVALUATION_TURN = 6
EVALUATION_RESET_TURN = 10

# Phase 6: score thresholds replacing the old HIGH/LOW_CONFIDENCE constants.
# Calibrated to the [0.0, 1.0] Scores range.
CLEAR_THRESHOLD = 0.3        # miscalibration & struggle both below → "clear-headed"
OVERLOAD_THRESHOLD = 0.7     # struggle or affect_load above → "overwhelmed"
# Acceleration gate: don't trust score-based clarity until the student has
# spent at least this many total turns. Prevents all-zero default scores from
# leaping to EVALUATION on the first MONITORING turn.
MIN_TURNS_BEFORE_ACCEL = 4


class PolicyFSM:
    def transition(self, ctx: PolicyContext) -> FSMState:
        """Pure FSM transition. Returns the next state given the current ctx."""
        state = ctx.current_state
        turn = ctx.turn_count
        scores = ctx.scores  # may be None when caller hasn't computed scores

        if state == FSMState.PLANNING:
            if turn >= PLANNING_TO_MONITORING_TURN:
                return FSMState.MONITORING
            return FSMState.PLANNING

        if state == FSMState.MONITORING:
            if turn >= MONITORING_TO_EVALUATION_TURN:
                return FSMState.EVALUATION
            # Score-based acceleration: needs an attempt signal AND clear
            # scores (low miscalibration, low struggle, low affect_load) AND
            # enough elapsed turns. The affect_load guard prevents a confused
            # learner (high affect_load but lowish struggle) from being
            # falsely promoted to EVALUATION — they're overwhelmed, not ready.
            if (
                scores is not None
                and turn >= MIN_TURNS_BEFORE_ACCEL
                and ctx.user_signals.attempt_present
                and scores.miscalibration < CLEAR_THRESHOLD
                and scores.struggle < CLEAR_THRESHOLD
                and scores.affect_load < CLEAR_THRESHOLD
            ):
                return FSMState.EVALUATION
            return FSMState.MONITORING

        if state == FSMState.EVALUATION:
            if turn >= EVALUATION_RESET_TURN:
                return FSMState.PLANNING
            if scores is not None and (
                scores.struggle > OVERLOAD_THRESHOLD
                or scores.affect_load > OVERLOAD_THRESHOLD
            ):
                return FSMState.PLANNING
            return FSMState.EVALUATION

        return state
