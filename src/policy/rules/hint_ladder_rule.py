"""
HintLadderRule — emits a prompt directive corresponding to the current
hint-ladder rung. Reads ctx.hint_state (which the engine has already advanced
this turn before rules run).

This rule is essential — it's the core scaffolding move per turn. It does not
override the planned question (the selector already chose one); it tells the
LLM how SPECIFIC its hint should be.

When hint_state == BOTTOM_OUT, this rule also relaxes
plan.constraints.forbid_direct_answer = False (resolves R7 from the spec —
the hint ladder bottom-out is intentionally a near-answer worked sub-step).
The DirectAnswerDetectorInterceptor reads forbid_direct_answer and skips
its check accordingly; the rhetorical interceptor still runs.
"""
from src.policy.rules.base import BaseRule
from src.policy.types import HintLadderState, PolicyContext, QuestionPlan

__evidence__ = [
    "koedinger_aleven_2007_assistance_dilemma",
    "narciss_2008_informative_tutoring_feedback",
    "hattie_timperley_2007_feedback",
]

_DIRECTIVES: dict[HintLadderState, str] = {
    HintLadderState.PROCESS_FEEDBACK: (
        "Use only PROCESS feedback. Comment briefly on the student's reasoning "
        "or method WITHOUT giving any concrete next step. End with the planned "
        "reflective question."
    ),
    HintLadderState.STRATEGIC_HINT: (
        "Provide a STRATEGIC hint. Point them in a useful direction without "
        "naming specific facts, formulas, or sub-steps. Then ask the planned "
        "reflective question."
    ),
    HintLadderState.FOCUSED_HINT: (
        "Provide a FOCUSED hint. Name the specific concept or sub-goal they "
        "should focus on, but do NOT solve any step for them. Then ask the "
        "planned reflective question."
    ),
    HintLadderState.BOTTOM_OUT: (
        "BOTTOM-OUT: the student has been stuck for several turns. Show ONE "
        "concrete worked sub-step (not the final answer), explain briefly why "
        "it works, and then ask a check question to verify they followed it. "
        "Do not finish the problem for them."
    ),
}


class HintLadderRule(BaseRule):
    essential = True

    def apply(self, ctx: PolicyContext, plan: QuestionPlan) -> str | None:
        directive = _DIRECTIVES.get(ctx.hint_state)
        if not directive:
            return None

        plan.prompt_directives.append(directive)

        if ctx.hint_state == HintLadderState.BOTTOM_OUT:
            # The bottom-out rung is intentionally a near-answer — relax the
            # direct-answer guardrail so the LLM can show a worked sub-step
            # without the interceptor over-correcting.
            plan.constraints.forbid_direct_answer = False

        return f"hint_ladder:{ctx.hint_state.value.lower()}"
