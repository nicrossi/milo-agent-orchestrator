# NOTE: confidence defaults to 3 (neutral) in v1, so this rule fires only for
# explicitly injected confidence values. The actual signal injection from the
# client is deferred (see R2 in spec.md).

from src.policy.rules.base import BaseRule
from src.policy.types import PolicyContext, QuestionPlan


class ToneByConfidenceRule(BaseRule):
    def apply(self, ctx: PolicyContext, plan: QuestionPlan) -> str | None:
        confidence = ctx.user_signals.confidence

        if confidence <= 2:
            plan.tone = "supportive"
            plan.prompt_directives.append(
                "The student seems to have low confidence. Use a warm, supportive tone. "
                "Validate their effort before asking a reflective question."
            )
            return "tone_by_confidence"

        if confidence >= 4:
            plan.tone = "challenging"
            plan.prompt_directives.append(
                "The student shows high confidence. Use a challenging tone. "
                "Push them to think deeper and justify their reasoning."
            )
            return "tone_by_confidence"

        # confidence == 3 → neutral, no modification
        return None
