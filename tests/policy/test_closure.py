"""
Tests for the LLM closure-sentinel directive injection in PolicyEngine.evaluate.

The engine is responsible for *making the sentinel eligible* — appending the
closure directive to plan.prompt_directives once the conversation is mature
enough that an LLM-judged "we're done" is plausible. The actual decision and
the post-stream sentinel parsing live in session.py and are not covered here.
"""
from src.policy.engine import (
    CLOSURE_MIN_TURNS,
    CLOSURE_SENTINEL,
    PolicyEngine,
)
from src.policy.types import FSMState, PolicyContext, UserSignals


def _ctx(state: FSMState, turn_count: int) -> PolicyContext:
    return PolicyContext(
        current_state=state,
        turn_count=turn_count,
        recent_question_ids=[],
        user_message="ok, creo que entiendo",
        user_signals=UserSignals(),
    )


engine = PolicyEngine()


def _has_closure_directive(directives: list[str]) -> bool:
    return any(CLOSURE_SENTINEL in d for d in directives)


def test_closure_directive_absent_at_planning():
    """At turn 0 the FSM stays in PLANNING — never invites closure."""
    decision = engine.evaluate(_ctx(FSMState.PLANNING, turn_count=0))
    assert decision.next_state == FSMState.PLANNING
    assert not _has_closure_directive(decision.plan.prompt_directives)


def test_closure_directive_absent_below_min_turns():
    """Even in MONITORING, fewer than CLOSURE_MIN_TURNS turns is too soon."""
    decision = engine.evaluate(_ctx(FSMState.MONITORING, turn_count=CLOSURE_MIN_TURNS - 1))
    assert not _has_closure_directive(decision.plan.prompt_directives)


def test_closure_directive_present_in_monitoring_at_min_turns():
    decision = engine.evaluate(_ctx(FSMState.MONITORING, turn_count=CLOSURE_MIN_TURNS))
    assert _has_closure_directive(decision.plan.prompt_directives)


def test_closure_directive_present_in_evaluation():
    """EVALUATION at turn 6 (FSM transition threshold) should include it."""
    decision = engine.evaluate(_ctx(FSMState.MONITORING, turn_count=6))
    assert decision.next_state == FSMState.EVALUATION
    assert _has_closure_directive(decision.plan.prompt_directives)


def test_closure_directive_suppressed_during_recovery():
    """While the student is in STABILIZE, wrapping up would be premature."""
    high_affect_window = [UserSignals(confusion=0.9, hedging=0.5) for _ in range(3)]
    ctx = PolicyContext(
        current_state=FSMState.MONITORING,
        turn_count=CLOSURE_MIN_TURNS + 2,
        recent_question_ids=[],
        user_message="no entiendo nada",
        user_signals=UserSignals(confusion=0.9, hedging=0.5),
        signals_window=high_affect_window,
    )
    decision = engine.evaluate(ctx)
    # Recovery state pauses FSM and should suppress closure invitation.
    assert not _has_closure_directive(decision.plan.prompt_directives)
