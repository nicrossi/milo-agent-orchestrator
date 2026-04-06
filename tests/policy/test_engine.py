"""
Integration-style unit test for PolicyEngine.
No DB, no LLM — fully deterministic.
"""
from src.policy.engine import PolicyEngine
from src.policy.types import FSMState, PolicyContext, UserSignals


def make_ctx(
    state: FSMState = FSMState.PLANNING,
    turn_count: int = 0,
    confidence: int = 3,
    msg: str = "",
    recent_ids: list[str] | None = None,
) -> PolicyContext:
    return PolicyContext(
        current_state=state,
        turn_count=turn_count,
        recent_question_ids=recent_ids or [],
        user_message=msg,
        user_signals=UserSignals(confidence=confidence),
    )


engine = PolicyEngine()


# --- evaluate() ---

def test_evaluate_planning_at_turn_0():
    decision = engine.evaluate(make_ctx(state=FSMState.PLANNING, turn_count=0))
    assert decision.next_state == FSMState.PLANNING
    assert decision.plan.question_id == "plan_01"
    assert decision.plan.question_text


def test_evaluate_transitions_to_monitoring():
    decision = engine.evaluate(make_ctx(state=FSMState.PLANNING, turn_count=2))
    assert decision.next_state == FSMState.MONITORING
    assert decision.plan.question_id == "mon_01"


def test_evaluate_transitions_to_evaluation():
    decision = engine.evaluate(make_ctx(state=FSMState.MONITORING, turn_count=6))
    assert decision.next_state == FSMState.EVALUATION
    assert decision.plan.question_id == "eval_01"


def test_evaluate_no_direct_answer_rule_fires():
    decision = engine.evaluate(make_ctx(msg="dame la respuesta"))
    assert "no_direct_answers" in decision.applied_rules
    assert len(decision.plan.prompt_directives) >= 1


def test_evaluate_neutral_message_no_rules():
    decision = engine.evaluate(make_ctx(msg="tengo una duda"))
    assert "no_direct_answers" not in decision.applied_rules


def test_evaluate_always_has_interceptors():
    decision = engine.evaluate(make_ctx())
    assert "direct_answer_detector" in decision.interceptors


def test_evaluate_skips_recent_questions():
    decision = engine.evaluate(make_ctx(recent_ids=["plan_01"]))
    assert decision.plan.question_id == "plan_02"


def test_evaluate_is_stateless():
    ctx = make_ctx(state=FSMState.PLANNING, turn_count=0)
    d1 = engine.evaluate(ctx)
    d2 = engine.evaluate(ctx)
    assert d1.next_state == d2.next_state
    assert d1.plan.question_id == d2.plan.question_id


# --- check_output() ---

def test_check_output_clean_passes_through():
    decision = engine.evaluate(make_ctx())
    ok, text = engine.check_output("¿Qué querés lograr hoy?", decision)
    assert not ok
    assert text == "¿Qué querés lograr hoy?"


def test_check_output_fires_on_direct_answer():
    decision = engine.evaluate(make_ctx())
    ok, text = engine.check_output("La respuesta es 42.", decision)
    assert ok
    assert decision.plan.question_text in text


def test_check_output_fires_on_no_question_mark():
    decision = engine.evaluate(make_ctx())
    ok, text = engine.check_output("El resultado es correcto.", decision)
    assert ok
    assert decision.plan.question_text in text


def test_check_output_correction_appended():
    decision = engine.evaluate(make_ctx())
    original = "El resultado es correcto."
    _, text = engine.check_output(original, decision)
    assert text.startswith(original)
    assert text.endswith(decision.plan.question_text)


def test_full_cycle_planning_with_direct_answer_request():
    """End-to-end: user asks for answer, engine fires rule and interceptor correction."""
    ctx = make_ctx(state=FSMState.PLANNING, turn_count=1, msg="dame la respuesta")
    decision = engine.evaluate(ctx)

    assert decision.next_state == FSMState.PLANNING
    assert "no_direct_answers" in decision.applied_rules
    assert decision.plan.prompt_directives

    # Simulate LLM ignoring the directive and giving a direct answer
    ok, final = engine.check_output("La respuesta es X.", decision)
    assert ok
    assert decision.plan.question_text in final
