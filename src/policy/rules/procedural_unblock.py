"""
ProceduralUnblockRule — Scaffolding Pivot for procedural prerequisite roadblocks.

Fires when the student has shown earlier engagement *and* repeatedly signals
a missing low-level fact (formula, definition) that is outside the activity's
pedagogical goal. The rule:

  1. Relaxes plan.constraints.forbid_direct_answer for THIS turn so the LLM
     can hand off the prerequisite. The DirectAnswerDetectorInterceptor reads
     forbid_direct_answer and skips its check accordingly. The rhetorical
     interceptor still runs.
  2. Appends a directive instructing the LLM to provide the missing fact in
     1-2 sentences with a single-line rationale, then immediately pivot to a
     question that requires applying the fact toward the activity goal.
  3. Replaces plan.question_text with a placeholder — the LLM regenerates
     the redirect question from the directive + activity goal.

Cooldown: the rule is gated by ctx.turns_since_procedural_unblock >= 2 so it
fires at most once every two turns. Avoids runaway prerequisite-spilling.

Designed to coexist with the hint ladder: this rule is orthogonal to the
metacognitive escalation. The ladder still advances on genuine pedagogical
struggle; this rule handles the distinct case of factual recall failure.
"""
from src.policy.rules.base import BaseRule
from src.policy.types import PolicyContext, QuestionPlan

__evidence__ = [
    "koedinger_aleven_2007_assistance_dilemma",
    "vygotsky_zpd_scaffolding",
    "dmello_graesser_2014_confusion_to_frustration",
]

# Score threshold — calibrated against the procedural_block formula in
# scores.py so the rule fires when the student has signaled procedural
# stuckness in roughly 2 of the last 4 turns AND showed an attempt earlier.
_FIRE_THRESHOLD = 0.45

# Cooldown — minimum turns between consecutive firings.
_COOLDOWN_TURNS = 2

_DIRECTIVE = (
    "SCAFFOLDING PIVOT (Unstucking Protocol). The student earlier showed real "
    "engagement with the activity, but is now stuck on a missing low-level "
    "fact (a formula, definition, or memory item) that is OUTSIDE the "
    "activity's pedagogical goal. Do this in one turn:\n"
    "  1. HAND OFF THE FACT in 1-2 sentences with a single-line rationale "
    "(e.g., 'Area = length × width — you're counting the unit squares that "
    "fit inside the shape.').\n"
    "  2. PIVOT TO THE GOAL. Reference the activity's pedagogical goal "
    "explicitly. Ask one question that forces the student to APPLY the "
    "just-given fact to the comparison the activity is built around. Use "
    "concrete numbers from the problem when available.\n"
    "Do not solve the activity. Do not lecture. The student must do the "
    "conceptual heavy lifting."
)

# When the rule fires we replace plan.question_text with this placeholder so
# downstream logging and metrics record the routing, but the LLM is expected
# to generate the actual redirect question per the directive.
_PLACEHOLDER_QUESTION = "[procedural_unblock: redirect to goal]"


class ProceduralUnblockRule(BaseRule):
    essential = True

    def apply(self, ctx: PolicyContext, plan: QuestionPlan) -> str | None:
        if ctx.scores is None:
            return None
        if ctx.scores.procedural_block <= _FIRE_THRESHOLD:
            return None
        if ctx.turns_since_procedural_unblock < _COOLDOWN_TURNS:
            return None

        # Replace the directive stack so we don't ship contradictory hints
        # ("comment without giving any concrete step" + "hand off the fact").
        # Earlier rules' directives are intentionally discarded for this turn.
        plan.prompt_directives = [_DIRECTIVE]
        plan.constraints.forbid_direct_answer = False
        plan.constraints.must_elicit_attempt = False
        plan.question_text = _PLACEHOLDER_QUESTION
        return "procedural_unblock"
