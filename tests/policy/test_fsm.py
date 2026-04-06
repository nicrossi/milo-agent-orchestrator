import pytest

from src.policy.fsm import PolicyFSM
from src.policy.types import FSMState, PolicyContext, UserSignals


def make_ctx(
    state: FSMState = FSMState.PLANNING,
    turn_count: int = 0,
    confidence: int = 3,
    msg: str = "",
) -> PolicyContext:
    return PolicyContext(
        current_state=state,
        turn_count=turn_count,
        recent_question_ids=[],
        user_message=msg,
        user_signals=UserSignals(confidence=confidence),
    )


fsm = PolicyFSM()


def test_planning_stays_planning_early():
    assert fsm.transition(make_ctx(state=FSMState.PLANNING, turn_count=1)) == FSMState.PLANNING


def test_planning_stays_planning_at_zero():
    assert fsm.transition(make_ctx(state=FSMState.PLANNING, turn_count=0)) == FSMState.PLANNING


def test_planning_to_monitoring_at_threshold():
    assert fsm.transition(make_ctx(state=FSMState.PLANNING, turn_count=2)) == FSMState.MONITORING


def test_planning_to_monitoring_above_threshold():
    assert fsm.transition(make_ctx(state=FSMState.PLANNING, turn_count=5)) == FSMState.MONITORING


def test_monitoring_stays_monitoring_below_threshold():
    assert fsm.transition(make_ctx(state=FSMState.MONITORING, turn_count=3)) == FSMState.MONITORING


def test_monitoring_to_evaluation_at_threshold():
    assert fsm.transition(make_ctx(state=FSMState.MONITORING, turn_count=6)) == FSMState.EVALUATION


def test_high_confidence_accelerates_to_evaluation():
    assert fsm.transition(make_ctx(state=FSMState.MONITORING, turn_count=3, confidence=4)) == FSMState.EVALUATION


def test_high_confidence_5_accelerates_to_evaluation():
    assert fsm.transition(make_ctx(state=FSMState.MONITORING, turn_count=3, confidence=5)) == FSMState.EVALUATION


def test_evaluation_stays_evaluation():
    assert fsm.transition(make_ctx(state=FSMState.EVALUATION, turn_count=7)) == FSMState.EVALUATION


def test_evaluation_resets_to_planning_at_threshold():
    assert fsm.transition(make_ctx(state=FSMState.EVALUATION, turn_count=10)) == FSMState.PLANNING


def test_evaluation_resets_to_planning_above_threshold():
    assert fsm.transition(make_ctx(state=FSMState.EVALUATION, turn_count=12)) == FSMState.PLANNING


def test_low_confidence_from_evaluation_resets():
    assert fsm.transition(make_ctx(state=FSMState.EVALUATION, turn_count=7, confidence=2)) == FSMState.PLANNING


def test_low_confidence_1_from_evaluation_resets():
    assert fsm.transition(make_ctx(state=FSMState.EVALUATION, turn_count=7, confidence=1)) == FSMState.PLANNING


def test_mid_confidence_evaluation_stays():
    assert fsm.transition(make_ctx(state=FSMState.EVALUATION, turn_count=7, confidence=3)) == FSMState.EVALUATION
