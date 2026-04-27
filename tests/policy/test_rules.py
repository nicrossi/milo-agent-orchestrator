import pytest

from src.policy.rules.no_direct_answers import NoDirectAnswersRule
from src.policy.rules.tone_by_confidence import ToneByConfidenceRule
from src.policy.types import (
    FSMState, PolicyContext, QuestionPlan, ResponseConstraints, Scores, UserSignals
)


def make_ctx(
    msg: str = "",
    state: FSMState = FSMState.PLANNING,
    user_signals: UserSignals | None = None,
    scores: Scores | None = None,
) -> PolicyContext:
    ctx = PolicyContext(
        current_state=state,
        turn_count=0,
        recent_question_ids=[],
        user_message=msg,
        user_signals=user_signals or UserSignals(),
    )
    # Phase 1: tone rule reads ctx.scores. Tests can pre-populate scores
    # directly for unit-level isolation (the engine populates scores at
    # runtime; rule tests are unit-scoped).
    ctx.scores = scores or Scores()
    return ctx


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


# --- ToneByConfidenceRule (Phase 1: score-driven, no longer raw confidence) ---

def test_tone_supportive_on_high_affect_load():
    plan = make_plan()
    ctx = make_ctx(scores=Scores(affect_load=0.7))
    result = ToneByConfidenceRule().apply(ctx, plan)
    assert result == "tone_by_confidence"
    assert plan.tone == "supportive"
    assert len(plan.prompt_directives) == 1


def test_tone_supportive_threshold_just_above():
    plan = make_plan()
    ctx = make_ctx(scores=Scores(affect_load=0.61))
    result = ToneByConfidenceRule().apply(ctx, plan)
    assert result == "tone_by_confidence"
    assert plan.tone == "supportive"


def test_tone_neutral_on_default_scores():
    plan = make_plan()
    ctx = make_ctx()  # all-zero Scores
    result = ToneByConfidenceRule().apply(ctx, plan)
    assert result is None
    assert plan.tone == "neutral"
    assert not plan.prompt_directives


def test_tone_neutral_on_mid_affect_load():
    plan = make_plan()
    ctx = make_ctx(scores=Scores(affect_load=0.5))
    result = ToneByConfidenceRule().apply(ctx, plan)
    assert result is None
    assert plan.tone == "neutral"


def test_tone_challenging_on_high_miscalibration_low_hedging():
    plan = make_plan()
    ctx = make_ctx(
        user_signals=UserSignals(hedging=0.0),
        scores=Scores(miscalibration=0.8),
    )
    result = ToneByConfidenceRule().apply(ctx, plan)
    assert result == "tone_by_confidence"
    assert plan.tone == "challenging"
    assert len(plan.prompt_directives) == 1


def test_tone_not_challenging_when_hedging_present():
    plan = make_plan()
    ctx = make_ctx(
        user_signals=UserSignals(hedging=0.5),  # hedging suppresses challenge
        scores=Scores(miscalibration=0.8),
    )
    result = ToneByConfidenceRule().apply(ctx, plan)
    assert result is None
    assert plan.tone == "neutral"


def test_tone_supportive_takes_priority_over_challenging():
    # If both conditions are met, supportive wins (affect_load checked first).
    plan = make_plan()
    ctx = make_ctx(
        user_signals=UserSignals(hedging=0.0),
        scores=Scores(affect_load=0.7, miscalibration=0.8),
    )
    result = ToneByConfidenceRule().apply(ctx, plan)
    assert result == "tone_by_confidence"
    assert plan.tone == "supportive"


def test_tone_no_op_when_scores_missing():
    # Defensive: when ctx.scores is None (shouldn't happen at runtime),
    # rule fails closed instead of crashing.
    plan = make_plan()
    ctx = PolicyContext(
        current_state=FSMState.PLANNING,
        turn_count=0,
        recent_question_ids=[],
        user_message="",
    )
    # ctx.scores is None by default
    result = ToneByConfidenceRule().apply(ctx, plan)
    assert result is None
    assert plan.tone == "neutral"
