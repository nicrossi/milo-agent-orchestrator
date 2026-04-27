"""Tests for ElicitAttemptRule."""
from src.policy.questions.bank import by_family
from src.policy.questions.families import QuestionFamily
from src.policy.rules.elicit_attempt import ElicitAttemptRule
from src.policy.types import (
    FSMState, PolicyContext, QuestionPlan, ResponseConstraints, UserSignals
)


def _attempt_pool():
    return by_family(QuestionFamily.ATTEMPT_ELICITATION)


def make_ctx(direct_ask: bool, attempt_present: bool, recent: list[str] = None) -> PolicyContext:
    return PolicyContext(
        current_state=FSMState.PLANNING,
        turn_count=0,
        recent_question_ids=recent or [],
        user_message="dame la respuesta" if direct_ask else "estoy pensando",
        user_signals=UserSignals(
            direct_answer_request=direct_ask,
            attempt_present=attempt_present,
        ),
    )


def make_plan() -> QuestionPlan:
    return QuestionPlan(
        question_id="plan_01",
        question_text="¿Qué querés lograr?",
        constraints=ResponseConstraints(),
    )


def test_fires_on_direct_ask_with_no_attempt():
    plan = make_plan()
    ctx = make_ctx(direct_ask=True, attempt_present=False)
    result = ElicitAttemptRule().apply(ctx, plan)
    assert result == "elicit_attempt"
    assert plan.constraints.must_elicit_attempt is True


def test_overrides_question_id_and_text():
    plan = make_plan()
    ctx = make_ctx(direct_ask=True, attempt_present=False)
    ElicitAttemptRule().apply(ctx, plan)
    # Question was replaced with one from the ATTEMPT_ELICITATION family.
    pool = _attempt_pool()
    pool_ids = [q.id for q in pool]
    assert plan.question_id in pool_ids
    assert plan.question_id != "plan_01"
    pool_texts = [v for q in pool for v in q.surface_variants]
    assert plan.question_text in pool_texts


def test_appends_directive():
    plan = make_plan()
    ctx = make_ctx(direct_ask=True, attempt_present=False)
    ElicitAttemptRule().apply(ctx, plan)
    assert len(plan.prompt_directives) == 1
    assert "demanding" in plan.prompt_directives[0].lower()


def test_does_not_fire_when_attempt_present():
    plan = make_plan()
    ctx = make_ctx(direct_ask=True, attempt_present=True)
    result = ElicitAttemptRule().apply(ctx, plan)
    assert result is None
    assert plan.question_id == "plan_01"  # unchanged
    assert plan.constraints.must_elicit_attempt is False


def test_does_not_fire_without_direct_ask():
    plan = make_plan()
    ctx = make_ctx(direct_ask=False, attempt_present=False)
    result = ElicitAttemptRule().apply(ctx, plan)
    assert result is None
    assert plan.question_id == "plan_01"


def test_skips_recent_question_ids():
    plan = make_plan()
    # Pretend elicit_01 was already asked.
    ctx = make_ctx(direct_ask=True, attempt_present=False, recent=["elicit_01"])
    ElicitAttemptRule().apply(ctx, plan)
    assert plan.question_id != "elicit_01"


def test_falls_back_to_first_when_all_recent():
    plan = make_plan()
    pool = _attempt_pool()
    all_ids = [q.id for q in pool]
    ctx = make_ctx(direct_ask=True, attempt_present=False, recent=all_ids)
    ElicitAttemptRule().apply(ctx, plan)
    assert plan.question_id == pool[0].id
