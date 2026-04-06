from src.policy.types import FSMState, PolicyContext

# Tunable threshold constants
PLANNING_TO_MONITORING_TURN = 2    # after 2 completed turns, advance to MONITORING
MONITORING_TO_EVALUATION_TURN = 6  # after 6 turns, advance to EVALUATION
EVALUATION_RESET_TURN = 10         # after 10 turns, reset to PLANNING (new cycle)
HIGH_CONFIDENCE_THRESHOLD = 4      # confidence >= 4 accelerates MONITORING → EVALUATION
LOW_CONFIDENCE_THRESHOLD = 2       # confidence <= 2 forces back to PLANNING from EVALUATION


class PolicyFSM:
    def transition(self, ctx: PolicyContext) -> FSMState:
        """
        Pure function — no side effects. Returns the next FSM state given the current context.

        Transition logic:
        - PLANNING  → MONITORING   when turn_count >= PLANNING_TO_MONITORING_TURN
        - MONITORING → EVALUATION  when turn_count >= MONITORING_TO_EVALUATION_TURN
                                   OR confidence >= HIGH_CONFIDENCE_THRESHOLD
        - EVALUATION → PLANNING    when turn_count >= EVALUATION_RESET_TURN
                                   OR confidence <= LOW_CONFIDENCE_THRESHOLD
        - Any state stays put if none of the above conditions are met.
        """
        state = ctx.current_state
        turn = ctx.turn_count
        confidence = ctx.user_signals.confidence

        if state == FSMState.PLANNING:
            if turn >= PLANNING_TO_MONITORING_TURN:
                return FSMState.MONITORING
            return FSMState.PLANNING

        if state == FSMState.MONITORING:
            if turn >= MONITORING_TO_EVALUATION_TURN or confidence >= HIGH_CONFIDENCE_THRESHOLD:
                return FSMState.EVALUATION
            return FSMState.MONITORING

        if state == FSMState.EVALUATION:
            if turn >= EVALUATION_RESET_TURN or confidence <= LOW_CONFIDENCE_THRESHOLD:
                return FSMState.PLANNING
            return FSMState.EVALUATION

        return state
