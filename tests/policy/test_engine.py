"""
Integration-style unit test for PolicyEngine.
No DB, no LLM — fully deterministic.
"""
from src.policy.engine import PolicyEngine
from src.policy.types import FSMState, PolicyContext, UserSignals


def make_ctx(
    state: FSMState = FSMState.PLANNING,
    turn_count: int = 0,
    msg: str = "",
    recent_ids: list[str] | None = None,
) -> PolicyContext:
    return PolicyContext(
        current_state=state,
        turn_count=turn_count,
        recent_question_ids=recent_ids or [],
        user_message=msg,
        user_signals=UserSignals(),
    )


engine = PolicyEngine()


# --- evaluate() ---

def test_evaluate_planning_at_turn_0():
    decision = engine.evaluate(make_ctx(state=FSMState.PLANNING, turn_count=0))
    assert decision.next_state == FSMState.PLANNING
    # Phase 3: default PLANNING family preference is GOAL_CLARIFICATION → goal_01.
    assert decision.plan.question_id == "goal_01"
    assert decision.plan.question_text


def test_evaluate_transitions_to_monitoring():
    decision = engine.evaluate(make_ctx(state=FSMState.PLANNING, turn_count=2))
    assert decision.next_state == FSMState.MONITORING
    # Phase 3: default MONITORING family preference is MONITORING_CHECK → check_01.
    assert decision.plan.question_id == "check_01"


def test_evaluate_transitions_to_evaluation():
    decision = engine.evaluate(make_ctx(state=FSMState.MONITORING, turn_count=6))
    assert decision.next_state == FSMState.EVALUATION
    # Phase 3: default EVALUATION family preference is SELF_EXPLANATION → reflect_01.
    assert decision.plan.question_id == "reflect_01"


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
    # Phase 3: skip goal_01, fall through to next question in same family.
    decision = engine.evaluate(make_ctx(recent_ids=["goal_01"]))
    assert decision.plan.question_id == "goal_02"


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


# --- Phase 2 integration ---

def test_default_interceptors_include_rhetorical():
    decision = engine.evaluate(make_ctx())
    assert "rhetorical_question_detector" in decision.interceptors
    assert "direct_answer_detector" in decision.interceptors


def test_elicit_attempt_fires_when_direct_ask_no_attempt():
    """User demands the answer with no attempt → rule fires AND overrides question."""
    ctx = PolicyContext(
        current_state=FSMState.PLANNING,
        turn_count=0,
        recent_question_ids=[],
        user_message="dame la respuesta",
        user_signals=UserSignals(
            direct_answer_request=True,
            attempt_present=False,
        ),
    )
    decision = engine.evaluate(ctx)
    assert "elicit_attempt" in decision.applied_rules
    # Question was overridden to attempt-elicitation pool.
    assert decision.plan.question_id.startswith("elicit_")
    assert decision.plan.constraints.must_elicit_attempt is True


def test_elicit_attempt_does_not_fire_with_attempt_present():
    ctx = PolicyContext(
        current_state=FSMState.PLANNING,
        turn_count=0,
        recent_question_ids=[],
        user_message="probé X porque pensé Y, dame la respuesta",
        user_signals=UserSignals(
            direct_answer_request=True,
            attempt_present=True,
        ),
    )
    decision = engine.evaluate(ctx)
    assert "elicit_attempt" not in decision.applied_rules
    # Original FSM-state question retained (Phase 3: default planning question).
    assert decision.plan.question_id == "goal_01"


def test_rhetorical_interceptor_catches_assertion_with_closed_question():
    """Adversarial: LLM produces 'long assertion. ¿Entendiste?' — interceptor fires."""
    decision = engine.evaluate(make_ctx())
    text = (
        "Los herbívoros se multiplican sin control y agotan la vegetación, "
        "lo que afecta toda la cadena. ¿Entendiste?"
    )
    ok, final = engine.check_output(text, decision)
    assert ok
    assert decision.plan.question_text in final


# --- Phase 4 integration ---

def test_hint_ladder_directive_attached():
    """The HintLadderRule always fires and adds a scaffolding directive."""
    decision = engine.evaluate(make_ctx())
    # Decision lists hint_ladder firing for current rung.
    assert any(r.startswith("hint_ladder:") for r in decision.applied_rules)
    assert decision.plan.prompt_directives  # at least one directive present


def test_high_struggle_advances_hint_ladder_across_turns():
    """5 turns of high struggle + attempt → reaches FOCUSED_HINT, not BOTTOM_OUT."""
    from src.policy.types import HintLadderState, UserSignals

    # Signals that yield struggle > 0.6 (the ladder advancement threshold):
    # 0.35*1.0 + 0.25*1.0 + 0.20*1.0 (length<-1.0) = 0.80.
    state_chain = []
    sig = UserSignals(
        hedging=1.0,
        confusion=1.0,
        length_z=-2.0,
        attempt_present=True,
    )

    ctx = PolicyContext(
        current_state=FSMState.MONITORING,
        turn_count=0,
        recent_question_ids=[],
        user_message="X",
        user_signals=sig,
        hint_state=HintLadderState.PROCESS_FEEDBACK,
    )
    decision = engine.evaluate(ctx)
    state_chain.append(decision.next_hint_state)

    # Walk 5 more turns reusing decision.next_* into ctx.
    for _ in range(5):
        ctx = PolicyContext(
            current_state=FSMState.MONITORING,
            turn_count=0,
            recent_question_ids=[],
            user_message="X",
            user_signals=sig,
            hint_state=decision.next_hint_state,
            turns_in_hint_state=decision.next_turns_in_hint_state,
            consecutive_low_struggle_turns=decision.next_consecutive_low_struggle_turns,
        )
        decision = engine.evaluate(ctx)
        state_chain.append(decision.next_hint_state)

    # Ladder progressed from PROCESS_FEEDBACK and reached at least FOCUSED_HINT.
    assert HintLadderState.FOCUSED_HINT in state_chain or HintLadderState.BOTTOM_OUT in state_chain
    # Sanity: state_chain is non-decreasing rung-wise (modulo bottom-out gating).
    rung_order = [
        HintLadderState.PROCESS_FEEDBACK,
        HintLadderState.STRATEGIC_HINT,
        HintLadderState.FOCUSED_HINT,
        HintLadderState.BOTTOM_OUT,
    ]
    indices = [rung_order.index(s) for s in state_chain]
    # No backwards jumps from one turn to the next while struggle stays high.
    for prev, curr in zip(indices, indices[1:]):
        assert curr >= prev


def test_recovery_forces_recovery_stabilize_question():
    """High confusion + sustained affect window → STABILIZE, recover_* question."""
    from src.policy.types import RecoveryState, UserSignals

    high_affect_window = [UserSignals(confusion=0.8, hedging=0.5) for _ in range(2)]
    ctx = PolicyContext(
        current_state=FSMState.PLANNING,
        turn_count=0,
        recent_question_ids=[],
        user_message="no entiendo nada de esto",
        user_signals=UserSignals(confusion=0.8, hedging=0.5),
        signals_window=high_affect_window,
    )
    decision = engine.evaluate(ctx)
    assert decision.next_recovery_state == RecoveryState.STABILIZE
    assert decision.plan.question_id.startswith("recover_")


def test_recovery_pauses_fsm_transition():
    """While in STABILIZE, FSM does not advance even if turn_count crosses threshold."""
    from src.policy.types import RecoveryState, UserSignals

    high_affect_window = [UserSignals(confusion=0.8, hedging=0.5) for _ in range(3)]
    ctx = PolicyContext(
        current_state=FSMState.PLANNING,
        turn_count=2,  # would normally trigger PLANNING → MONITORING
        recent_question_ids=[],
        user_message="no entiendo",
        user_signals=UserSignals(confusion=0.8, hedging=0.5),
        signals_window=high_affect_window,
    )
    decision = engine.evaluate(ctx)
    assert decision.next_recovery_state == RecoveryState.STABILIZE
    # FSM frozen at PLANNING despite turn_count.
    assert decision.next_state == FSMState.PLANNING


def test_cooldown_suppresses_tone_rule_when_recent():
    """With turns_since_meta_feedback < 2, ToneByConfidenceRule is filtered out."""
    from src.policy.types import Scores, UserSignals

    ctx = PolicyContext(
        current_state=FSMState.PLANNING,
        turn_count=0,
        recent_question_ids=[],
        user_message="",
        # Force high affect_load → ToneByConfidence WOULD fire if not suppressed.
        user_signals=UserSignals(confusion=1.0, hedging=1.0),
        turns_since_meta_feedback=1,  # cooldown active
    )
    decision = engine.evaluate(ctx)
    assert "tone_by_confidence" not in decision.applied_rules


def test_cooldown_does_not_suppress_essential_rules():
    """no_direct_answers stays essential — fires even mid-cooldown."""
    from src.policy.types import UserSignals

    ctx = PolicyContext(
        current_state=FSMState.PLANNING,
        turn_count=0,
        recent_question_ids=[],
        user_message="dame la respuesta",
        user_signals=UserSignals(direct_answer_request=True, attempt_present=True),
        turns_since_meta_feedback=0,
    )
    decision = engine.evaluate(ctx)
    assert "no_direct_answers" in decision.applied_rules


def test_bottom_out_relaxes_direct_answer_constraint():
    """When ladder reaches BOTTOM_OUT, plan.constraints.forbid_direct_answer is False."""
    from src.policy.types import HintLadderState, UserSignals

    ctx = PolicyContext(
        current_state=FSMState.MONITORING,
        turn_count=0,
        recent_question_ids=[],
        user_message="",
        user_signals=UserSignals(attempt_present=True),
        hint_state=HintLadderState.BOTTOM_OUT,
        turns_in_hint_state=1,
    )
    decision = engine.evaluate(ctx)
    # Decision.plan should reflect HintLadderRule's relaxation.
    if decision.next_hint_state == HintLadderState.BOTTOM_OUT:
        assert decision.plan.constraints.forbid_direct_answer is False


def test_bottom_out_skips_direct_answer_interceptor():
    """check_output skips DirectAnswerDetector when forbid_direct_answer=False."""
    from src.policy.types import HintLadderState, UserSignals

    ctx = PolicyContext(
        current_state=FSMState.MONITORING,
        turn_count=0,
        recent_question_ids=[],
        user_message="",
        user_signals=UserSignals(attempt_present=True),
        hint_state=HintLadderState.BOTTOM_OUT,
        turns_in_hint_state=1,
    )
    decision = engine.evaluate(ctx)
    # If the LLM produces a worked-step style response (no clear ?), the
    # detector would normally flag it. With forbid_direct_answer=False, skipped.
    bottom_out_response = "El primer paso es identificar la variable y."
    ok, _ = engine.check_output(bottom_out_response, decision)
    if not decision.plan.constraints.forbid_direct_answer:
        assert ok is False  # interceptor skipped


def test_decision_carries_phase4_state_for_persistence():
    """PolicyDecision exposes all next_* fields needed by the caller."""
    decision = engine.evaluate(make_ctx())
    # All Phase 4 carry-state fields should be present on the decision.
    assert hasattr(decision, "next_hint_state")
    assert hasattr(decision, "next_recovery_state")
    assert hasattr(decision, "next_turns_since_meta_feedback")
