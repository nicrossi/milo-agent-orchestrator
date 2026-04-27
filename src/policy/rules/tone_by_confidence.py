"""
ToneByConfidenceRule — adjusts response tone based on derived Scores.

The rule reads ctx.scores (populated by PolicyEngine.evaluate before rules run):
  - affect_load > 0.6                              → supportive tone
  - miscalibration > 0.6 AND hedging < 0.2         → challenging tone
  - otherwise                                       → no modification (neutral)

The legacy `confidence` field on UserSignals is no longer consulted.
"""
from src.policy.rules.base import BaseRule
from src.policy.types import PolicyContext, QuestionPlan

__evidence__ = ["hattie_timperley_2007_feedback"]

_AFFECT_LOAD_SUPPORTIVE = 0.6
_MISCALIBRATION_CHALLENGING = 0.6
_HEDGING_CONFIDENT = 0.2


class ToneByConfidenceRule(BaseRule):
    # Phase 4: tone shifts are cosmetic meta-feedback, not a hard guardrail.
    # The MetaFeedbackCooldown rate-limits firings to ≤ 1 per 2 turns.
    essential = False

    def apply(self, ctx: PolicyContext, plan: QuestionPlan) -> str | None:
        scores = ctx.scores
        if scores is None:
            # Defensive: should never happen because engine populates scores
            # before rules run. Fail closed (no modification) instead of crash.
            return None

        if scores.affect_load > _AFFECT_LOAD_SUPPORTIVE:
            plan.tone = "supportive"
            plan.prompt_directives.append(
                "The student shows signs of low confidence and cognitive load. "
                "Use a warm, supportive tone. Validate their effort before "
                "asking a reflective question."
            )
            return "tone_by_confidence"

        if (
            scores.miscalibration > _MISCALIBRATION_CHALLENGING
            and ctx.user_signals.hedging < _HEDGING_CONFIDENT
        ):
            plan.tone = "challenging"
            plan.prompt_directives.append(
                "The student sounds confident but signals suggest "
                "miscalibration. Use a challenging tone. Push them to "
                "justify their reasoning and surface their assumptions."
            )
            return "tone_by_confidence"

        return None
