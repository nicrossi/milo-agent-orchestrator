"""FSM transition tests — Phase 6: score-driven (no longer raw confidence)."""
import pytest

from src.policy.fsm import PolicyFSM
from src.policy.types import FSMState, PolicyContext, Scores, UserSignals


def make_ctx(
    state: FSMState = FSMState.PLANNING,
    turn_count: int = 0,
    scores: Scores | None = None,
    msg: str = "",
) -> PolicyContext:
    ctx = PolicyContext(
        current_state=state,
        turn_count=turn_count,
        recent_question_ids=[],
        user_message=msg,
        user_signals=UserSignals(),
    )
    # The engine populates ctx.scores before the FSM transition; mirror that
    # in tests so we can drive specific score-based behaviors.
    ctx.scores = scores if scores is not None else Scores()
    return ctx


fsm = PolicyFSM()


# --- PLANNING (turn-count only, no score dependency) ---

def test_planning_stays_planning_early():
    assert fsm.transition(make_ctx(state=FSMState.PLANNING, turn_count=1)) == FSMState.PLANNING


def test_planning_stays_planning_at_zero():
    assert fsm.transition(make_ctx(state=FSMState.PLANNING, turn_count=0)) == FSMState.PLANNING


def test_planning_to_monitoring_at_threshold():
    assert fsm.transition(make_ctx(state=FSMState.PLANNING, turn_count=2)) == FSMState.MONITORING


def test_planning_to_monitoring_above_threshold():
    assert fsm.transition(make_ctx(state=FSMState.PLANNING, turn_count=5)) == FSMState.MONITORING


# --- MONITORING ---

def test_monitoring_stays_monitoring_below_threshold():
    assert fsm.transition(make_ctx(state=FSMState.MONITORING, turn_count=3)) == FSMState.MONITORING


def test_monitoring_to_evaluation_at_threshold():
    assert fsm.transition(make_ctx(state=FSMState.MONITORING, turn_count=6)) == FSMState.EVALUATION


def test_clear_signals_accelerate_to_evaluation():
    """Phase 6: clear signals + attempt + enough turns → MONITORING → EVALUATION early."""
    clear = Scores(miscalibration=0.1, struggle=0.1)
    ctx = make_ctx(state=FSMState.MONITORING, turn_count=4, scores=clear)
    ctx.user_signals.attempt_present = True
    assert fsm.transition(ctx) == FSMState.EVALUATION


def test_clear_signals_below_min_turns_no_acceleration():
    """Acceleration gated by MIN_TURNS_BEFORE_ACCEL (=4). turn=3 stays MONITORING."""
    clear = Scores(miscalibration=0.1, struggle=0.1)
    ctx = make_ctx(state=FSMState.MONITORING, turn_count=3, scores=clear)
    ctx.user_signals.attempt_present = True
    assert fsm.transition(ctx) == FSMState.MONITORING


def test_clear_signals_without_attempt_no_acceleration():
    """No attempt_present → don't accelerate (clarity without engagement is suspicious)."""
    clear = Scores(miscalibration=0.1, struggle=0.1)
    ctx = make_ctx(state=FSMState.MONITORING, turn_count=4, scores=clear)
    ctx.user_signals.attempt_present = False
    assert fsm.transition(ctx) == FSMState.MONITORING


def test_high_struggle_does_not_accelerate():
    """One side high → don't accelerate."""
    blocked = Scores(miscalibration=0.1, struggle=0.5)
    ctx = make_ctx(state=FSMState.MONITORING, turn_count=4, scores=blocked)
    ctx.user_signals.attempt_present = True
    assert fsm.transition(ctx) == FSMState.MONITORING


def test_high_miscalibration_does_not_accelerate():
    blocked = Scores(miscalibration=0.5, struggle=0.1)
    ctx = make_ctx(state=FSMState.MONITORING, turn_count=4, scores=blocked)
    ctx.user_signals.attempt_present = True
    assert fsm.transition(ctx) == FSMState.MONITORING


def test_high_affect_load_does_not_accelerate():
    """A confused learner (high affect_load) must NOT be falsely advanced.

    Regression: in early Phase 6 acceptance testing a 'estoy perdido, no
    tiene sentido' message produced struggle≈0.25, miscalibration=0,
    affect_load=0.6 — the FSM accelerated to EVALUATION even though the
    learner was overloaded. Fixed by adding affect_load < CLEAR_THRESHOLD
    to the acceleration gate.
    """
    overloaded = Scores(miscalibration=0.0, struggle=0.2, affect_load=0.6)
    ctx = make_ctx(state=FSMState.MONITORING, turn_count=4, scores=overloaded)
    ctx.user_signals.attempt_present = True
    assert fsm.transition(ctx) == FSMState.MONITORING


# --- EVALUATION ---

def test_evaluation_stays_evaluation():
    assert fsm.transition(make_ctx(state=FSMState.EVALUATION, turn_count=7)) == FSMState.EVALUATION


def test_evaluation_resets_to_planning_at_threshold():
    assert fsm.transition(make_ctx(state=FSMState.EVALUATION, turn_count=10)) == FSMState.PLANNING


def test_evaluation_resets_to_planning_above_threshold():
    assert fsm.transition(make_ctx(state=FSMState.EVALUATION, turn_count=12)) == FSMState.PLANNING


def test_high_struggle_in_evaluation_resets():
    """Phase 6: struggle > OVERLOAD_THRESHOLD forces EVALUATION → PLANNING."""
    overloaded = Scores(struggle=0.8)
    assert fsm.transition(
        make_ctx(state=FSMState.EVALUATION, turn_count=7, scores=overloaded)
    ) == FSMState.PLANNING


def test_high_affect_load_in_evaluation_resets():
    """Phase 6: affect_load > OVERLOAD_THRESHOLD forces EVALUATION → PLANNING."""
    overloaded = Scores(affect_load=0.8)
    assert fsm.transition(
        make_ctx(state=FSMState.EVALUATION, turn_count=7, scores=overloaded)
    ) == FSMState.PLANNING


def test_neutral_evaluation_stays():
    assert fsm.transition(
        make_ctx(state=FSMState.EVALUATION, turn_count=7, scores=Scores())
    ) == FSMState.EVALUATION


# --- robustness: scores=None ---

def test_transition_handles_none_scores_gracefully():
    """When scores hasn't been populated, FSM falls back to turn-count rules only."""
    ctx = PolicyContext(
        current_state=FSMState.MONITORING,
        turn_count=3,
        recent_question_ids=[],
        user_message="",
    )
    # ctx.scores is None by default
    assert fsm.transition(ctx) == FSMState.MONITORING
