import pytest

from src.policy.rules.no_direct_answers import NoDirectAnswersRule
from src.policy.rules.tone_by_confidence import ToneByConfidenceRule
from src.policy.types import (
    FSMState, PolicyContext, QuestionPlan, ResponseConstraints, UserSignals
)


def make_ctx(
    msg: str = "",
    confidence: int = 3,
    state: FSMState = FSMState.PLANNING,
) -> PolicyContext:
    return PolicyContext(
        current_state=state,
        turn_count=0,
        recent_question_ids=[],
        user_message=msg,
        user_signals=UserSignals(confidence=confidence),
    )


def make_plan() -> QuestionPlan:
    return QuestionPlan(
        question_id="plan_01",
        question_text="¿Qué es lo que querés lograr?",
        constraints=ResponseConstraints(),
    )


# --- NoDirectAnswersRule ---

def test_no_direct_answers_triggers_on_spanish():
    plan = make_plan()
    result = NoDirectAnswersRule().apply(make_ctx(msg="dame la respuesta"), plan)
    assert result == "no_direct_answers"
    assert plan.constraints.forbid_direct_answer is True
    assert len(plan.prompt_directives) == 1


def test_no_direct_answers_triggers_on_dime():
    plan = make_plan()
    result = NoDirectAnswersRule().apply(make_ctx(msg="dime la respuesta"), plan)
    assert result == "no_direct_answers"


def test_no_direct_answers_triggers_on_english():
    plan = make_plan()
    result = NoDirectAnswersRule().apply(make_ctx(msg="give me the answer"), plan)
    assert result == "no_direct_answers"
    assert len(plan.prompt_directives) == 1


def test_no_direct_answers_triggers_case_insensitive():
    plan = make_plan()
    result = NoDirectAnswersRule().apply(make_ctx(msg="DAME LA RESPUESTA"), plan)
    assert result == "no_direct_answers"


def test_no_direct_answers_does_not_trigger_on_neutral():
    plan = make_plan()
    result = NoDirectAnswersRule().apply(make_ctx(msg="tengo una duda"), plan)
    assert result is None
    assert not plan.prompt_directives


def test_no_direct_answers_does_not_trigger_on_empty():
    plan = make_plan()
    result = NoDirectAnswersRule().apply(make_ctx(msg=""), plan)
    assert result is None


def test_no_direct_answers_does_not_fire_twice():
    plan = make_plan()
    rule = NoDirectAnswersRule()
    rule.apply(make_ctx(msg="dame la respuesta"), plan)
    # Only one directive appended per apply call
    assert len(plan.prompt_directives) == 1


# --- ToneByConfidenceRule ---

def test_tone_supportive_on_low_confidence():
    plan = make_plan()
    result = ToneByConfidenceRule().apply(make_ctx(confidence=1), plan)
    assert result == "tone_by_confidence"
    assert plan.tone == "supportive"
    assert len(plan.prompt_directives) == 1


def test_tone_supportive_on_confidence_2():
    plan = make_plan()
    result = ToneByConfidenceRule().apply(make_ctx(confidence=2), plan)
    assert result == "tone_by_confidence"
    assert plan.tone == "supportive"


def test_tone_neutral_on_mid_confidence():
    plan = make_plan()
    result = ToneByConfidenceRule().apply(make_ctx(confidence=3), plan)
    assert result is None
    assert plan.tone == "neutral"
    assert not plan.prompt_directives


def test_tone_challenging_on_high_confidence():
    plan = make_plan()
    result = ToneByConfidenceRule().apply(make_ctx(confidence=4), plan)
    assert result == "tone_by_confidence"
    assert plan.tone == "challenging"
    assert len(plan.prompt_directives) == 1


def test_tone_challenging_on_confidence_5():
    plan = make_plan()
    result = ToneByConfidenceRule().apply(make_ctx(confidence=5), plan)
    assert result == "tone_by_confidence"
    assert plan.tone == "challenging"
