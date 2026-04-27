"""Tests for src/policy/questions/selector.py — score-driven family preference."""
from src.policy.questions.bank import by_id
from src.policy.questions.families import QuestionFamily
from src.policy.questions.selector import family_preference, select_question
from src.policy.types import ActivityRef, FSMState, Scores, UserSignals


# --- family_preference ---

def test_planning_default_prefers_goal_clarification():
    prefs = family_preference(FSMState.PLANNING, scores=None)
    assert prefs[0] == QuestionFamily.GOAL_CLARIFICATION


def test_monitoring_default_prefers_monitoring_check():
    prefs = family_preference(FSMState.MONITORING, scores=None)
    assert prefs[0] == QuestionFamily.MONITORING_CHECK


def test_evaluation_default_prefers_self_explanation():
    prefs = family_preference(FSMState.EVALUATION, scores=None)
    assert prefs[0] == QuestionFamily.SELF_EXPLANATION


def test_high_miscalibration_steers_to_calibration():
    scores = Scores(miscalibration=0.8)
    prefs = family_preference(FSMState.MONITORING, scores=scores)
    assert prefs[0] == QuestionFamily.CALIBRATION
    assert QuestionFamily.DISCREPANCY_DETECTION in prefs


def test_high_affect_load_steers_to_simpler_family():
    scores = Scores(affect_load=0.8)
    prefs = family_preference(FSMState.PLANNING, scores=scores)
    # First preference should be the most validating / lowest cognitive load.
    assert prefs[0] == QuestionFamily.GOAL_CLARIFICATION


def test_high_struggle_in_monitoring_prefers_self_explanation():
    scores = Scores(struggle=0.7)
    prefs = family_preference(FSMState.MONITORING, scores=scores)
    assert prefs[0] == QuestionFamily.SELF_EXPLANATION


# --- select_question ---

def test_select_returns_first_preferred_family_question():
    q, variant = select_question(
        state=FSMState.PLANNING,
        scores=None,
        recent_ids=[],
    )
    assert q.id == "goal_01"
    assert variant == q.surface_variants[0]


def test_select_skips_recent_within_family():
    q, _ = select_question(
        state=FSMState.PLANNING,
        scores=None,
        recent_ids=["goal_01"],
    )
    assert q.id == "goal_02"


def test_select_falls_through_to_next_family_when_first_exhausted():
    # Mark all GOAL_CLARIFICATION as recent → selector should pick STRATEGY_REVISION.
    goal_ids = ["goal_01", "goal_02", "goal_03"]
    q, _ = select_question(
        state=FSMState.PLANNING,
        scores=None,
        recent_ids=goal_ids,
    )
    assert q.family != QuestionFamily.GOAL_CLARIFICATION


def test_select_with_high_miscalibration_picks_calibration_in_monitoring():
    q, _ = select_question(
        state=FSMState.MONITORING,
        scores=Scores(miscalibration=0.8),
        recent_ids=[],
    )
    assert q.family == QuestionFamily.CALIBRATION


def test_select_respects_requires_attempt_when_attempt_absent():
    # A question requiring attempt should be skipped when user_signals say no attempt.
    # explain_01 requires_attempt=True; goal_01 does not.
    q, _ = select_question(
        state=FSMState.MONITORING,
        scores=Scores(struggle=0.7),  # would prefer SELF_EXPLANATION (explain_*)
        recent_ids=[],
        user_signals=UserSignals(attempt_present=False),
    )
    # Should NOT pick a requires_attempt=True question.
    if q.requires_attempt:
        assert False, f"Picked {q.id} which requires attempt, but signals say none"


def test_select_picks_requires_attempt_when_attempt_present():
    q, _ = select_question(
        state=FSMState.MONITORING,
        scores=Scores(struggle=0.7),
        recent_ids=[],
        user_signals=UserSignals(attempt_present=True),
    )
    assert q.family == QuestionFamily.SELF_EXPLANATION


def test_select_round_robin_falls_back_when_all_recent():
    # Mark every PLANNING question as recent.
    from src.policy.questions.bank import by_state
    all_planning_ids = [q.id for q in by_state(FSMState.PLANNING)]
    q, _ = select_question(
        state=FSMState.PLANNING,
        scores=None,
        recent_ids=all_planning_ids,
    )
    # Should not crash; falls back to first.
    assert q is not None


def test_select_returns_question_object_with_metadata():
    q, _ = select_question(
        state=FSMState.PLANNING,
        scores=None,
        recent_ids=[],
    )
    assert q.id
    assert q.family
    assert q.state == FSMState.PLANNING
    assert q.surface_variants


def test_select_evaluation_default():
    q, _ = select_question(
        state=FSMState.EVALUATION,
        scores=None,
        recent_ids=[],
    )
    assert q.id == "reflect_01"
    assert q.family == QuestionFamily.SELF_EXPLANATION
