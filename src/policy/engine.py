"""
PolicyEngine — two-phase per-turn policy orchestrator.

Phase 1 (pre-LLM): evaluate(ctx) → PolicyDecision
  Runs the per-turn pipeline:
    1. compute scores from signals
    2. recovery state transition (may pause FSM / force question family)
    3. FSM transition (paused if in STABILIZE)
    4. hint ladder transition (frozen if in STABILIZE)
    5. question selection (forced to RECOVERY_STABILIZE family if recovering)
    6. rules with cooldown filtering for non-essential rules
  Returns a PolicyDecision with directives, scores, and the next-turn cross-
  state values for the caller (session) to persist.

Phase 2 (post-LLM): check_output(raw, decision) → (was_modified, final_text)
  Runs interceptors. The DirectAnswerDetector is skipped when
  decision.plan.constraints.forbid_direct_answer is False (which the
  HintLadderRule sets in BOTTOM_OUT — the rung is intentionally a near-answer).
"""
from src.policy.cooldown import MetaFeedbackCooldown
from src.policy.fsm import PolicyFSM
from src.policy.hint_ladder import next_step as hint_next_step
from src.policy.interceptors.base import BaseOutputInterceptor
from src.policy.interceptors.direct_answer_detector import DirectAnswerDetectorInterceptor
from src.policy.interceptors.rhetorical_question_detector import (
    RhetoricalQuestionDetectorInterceptor,
)
from src.policy.questions.contextualizer import contextualize
from src.policy.questions.families import QuestionFamily
from src.policy.questions.selector import select_question
from src.policy.recovery import next_state as recovery_next_state
from src.policy.rules.base import BaseRule
from src.policy.rules.elicit_attempt import ElicitAttemptRule
from src.policy.rules.hint_ladder_rule import HintLadderRule
from src.policy.rules.no_direct_answers import NoDirectAnswersRule
from src.policy.rules.tone_by_confidence import ToneByConfidenceRule
from src.policy.scores import compute_scores
from src.policy.types import (
    PolicyContext,
    PolicyDecision,
    QuestionPlan,
    RecoveryState,
    ResponseConstraints,
)

# Module-level singletons — stateless, safe to share across all sessions.
# Rule order matters: ElicitAttemptRule runs first so it can override the
# planned question before downstream rules attach directives. HintLadderRule
# always runs to attach the rung-appropriate scaffold directive.
_fsm = PolicyFSM()
_rules: list[BaseRule] = [
    ElicitAttemptRule(),     # essential — routes turn
    NoDirectAnswersRule(),   # essential — guardrail
    HintLadderRule(),        # essential — scaffold directive
    ToneByConfidenceRule(),  # non-essential — cosmetic, cooldown-filtered
]
_interceptors: dict[str, BaseOutputInterceptor] = {
    "direct_answer_detector": DirectAnswerDetectorInterceptor(),
    "rhetorical_question_detector": RhetoricalQuestionDetectorInterceptor(),
}
# Default interceptor order: rhetorical FIRST (catches assertion+rhetorical
# patterns where direct_answer_detector would otherwise mis-classify the
# response as having an open question). If rhetorical fires, the corrected
# text already has an appended Socratic question, so direct_answer_detector
# becomes a no-op on the second pass.
_DEFAULT_INTERCEPTORS = ["rhetorical_question_detector", "direct_answer_detector"]


class PolicyEngine:
    """
    Stateless orchestrator — safe to share across sessions as a module-level singleton.
    All mutable state is in PolicyContext (caller-owned).
    """

    def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        # 1. Compute scores
        ctx.scores = compute_scores(ctx.signals_window, ctx.user_signals)

        # 2. Recovery state transition (uses scores + window)
        next_recovery, next_turns_in_rec = recovery_next_state(
            current=ctx.recovery_state,
            turns_in_recovery=ctx.turns_in_recovery,
            scores=ctx.scores,
            user_signals=ctx.user_signals,
            signals_window=ctx.signals_window,
        )

        # 3. FSM transition — paused while in STABILIZE.
        if next_recovery == RecoveryState.STABILIZE:
            next_state = ctx.current_state
        else:
            next_state = _fsm.transition(ctx)

        # 4. Hint ladder transition — frozen while in STABILIZE.
        next_hint, next_turns_in_hs, next_low = hint_next_step(
            current=ctx.hint_state,
            turns_in_state=ctx.turns_in_hint_state,
            consecutive_low_struggle=ctx.consecutive_low_struggle_turns,
            scores=ctx.scores,
            user_signals=ctx.user_signals,
            recovery_state=next_recovery,
        )
        # Mutate ctx so the HintLadderRule sees this turn's ladder state.
        ctx.hint_state = next_hint

        # 5. Question selection — recovery forces RECOVERY_STABILIZE family.
        if next_recovery == RecoveryState.STABILIZE:
            question, variant = select_question(
                state=next_state,
                scores=ctx.scores,
                recent_ids=ctx.recent_question_ids,
                activity=ctx.activity,
                user_signals=ctx.user_signals,
                force_family=QuestionFamily.RECOVERY_STABILIZE,
            )
        else:
            question, variant = select_question(
                state=next_state,
                scores=ctx.scores,
                recent_ids=ctx.recent_question_ids,
                activity=ctx.activity,
                user_signals=ctx.user_signals,
            )
        qtext = contextualize(variant, ctx.activity)

        plan = QuestionPlan(
            question_id=question.id,
            question_text=qtext,
            tone=question.tone,
            constraints=ResponseConstraints(),
        )

        # 6. Rules with cooldown filtering on non-essential ones.
        cooldown = MetaFeedbackCooldown(ctx.turns_since_meta_feedback)
        applied: list[str] = []
        any_non_essential_fired = False
        for rule in _rules:
            if not rule.essential and not cooldown.allows_intervention():
                continue
            result = rule.apply(ctx, plan)
            if result:
                applied.append(result)
                if not rule.essential:
                    any_non_essential_fired = True

        return PolicyDecision(
            next_state=next_state,
            plan=plan,
            applied_rules=applied,
            interceptors=list(_DEFAULT_INTERCEPTORS),
            scores=ctx.scores,
            next_hint_state=next_hint,
            next_turns_in_hint_state=next_turns_in_hs,
            next_consecutive_low_struggle_turns=next_low,
            next_recovery_state=next_recovery,
            next_turns_in_recovery=next_turns_in_rec,
            next_turns_since_meta_feedback=cooldown.compute_next(any_non_essential_fired),
        )

    def check_output(self, raw: str, decision: PolicyDecision) -> tuple[bool, str]:
        """
        Run post-LLM interceptors on the accumulated response.

        Skips DirectAnswerDetector when the plan explicitly relaxes
        forbid_direct_answer (set by HintLadderRule in BOTTOM_OUT — the rung
        is a worked sub-step by design). The rhetorical interceptor still runs.
        """
        text = raw
        was_modified = False
        for name in decision.interceptors:
            if name == "direct_answer_detector" and not decision.plan.constraints.forbid_direct_answer:
                continue
            if interceptor := _interceptors.get(name):
                modified, text = interceptor.process(text, decision.plan.question_text)
                if modified:
                    was_modified = True
        return was_modified, text
