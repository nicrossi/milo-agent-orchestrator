"""
ElicitAttemptRule — fires when the learner demands the answer without having
made an attempt. Routes the turn into attempt-elicitation instead of letting
NoDirectAnswers merely tag a directive.

Triggers when both:
  - ctx.user_signals.direct_answer_request is True
  - ctx.user_signals.attempt_present is False

Effect:
  - Sets plan.constraints.must_elicit_attempt = True
  - Replaces plan.question_text and question_id with one from the
    ATTEMPT_ELICITATION family (Phase 3 bank, round-robin via recent_ids).
  - Appends a strong prompt directive that overrides the FSM's default
    pedagogical move for this turn.
"""
from src.policy.questions.bank import by_family
from src.policy.questions.contextualizer import contextualize
from src.policy.questions.families import QuestionFamily
from src.policy.rules.base import BaseRule
from src.policy.types import PolicyContext, QuestionPlan

__evidence__ = ["aleven_koedinger_1999_explanation_transfer"]


def _select_attempt_question(recent_ids: list[str]):
    """Round-robin pick from ATTEMPT_ELICITATION family."""
    candidates = by_family(QuestionFamily.ATTEMPT_ELICITATION)
    for q in candidates:
        if q.id not in recent_ids:
            return q
    # Fallback: cycle to first if all asked.
    return candidates[0]


class ElicitAttemptRule(BaseRule):
    def apply(self, ctx: PolicyContext, plan: QuestionPlan) -> str | None:
        signals = ctx.user_signals
        if not signals.direct_answer_request:
            return None
        if signals.attempt_present:
            return None

        question = _select_attempt_question(ctx.recent_question_ids)
        plan.question_id = question.id
        plan.question_text = contextualize(question.surface_variants[0], ctx.activity)
        plan.tone = question.tone

        plan.constraints.must_elicit_attempt = True
        plan.constraints.forbid_direct_answer = True
        plan.prompt_directives.append(
            "The student is demanding the answer without having made an attempt. "
            "Do NOT explain or hint at the answer. Instead, prompt them to share "
            "their first idea, what they've already tried, or how they would start. "
            "Reduce cognitive load by offering a very small, concrete starting point "
            "if needed, but never the answer itself."
        )
        return "elicit_attempt"
