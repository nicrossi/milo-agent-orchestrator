"""
PolicyEngine — two-phase per-turn policy orchestrator.

Phase 1 (pre-LLM): evaluate(ctx) → PolicyDecision
  Runs: FSM transition → question selection → rules → configure interceptors.
  Returns a PolicyDecision containing prompt directives to inject into LLM context.

Phase 2 (post-LLM): check_output(raw, decision) → (was_modified, final_text)
  Runs all interceptors named in decision.interceptors against the accumulated LLM output.
  If a violation is detected, the corrected text is returned.
"""
from src.policy.fsm import PolicyFSM
from src.policy.interceptors.base import BaseOutputInterceptor
from src.policy.interceptors.direct_answer_detector import DirectAnswerDetectorInterceptor
from src.policy.question_bank import select_question
from src.policy.rules.base import BaseRule
from src.policy.rules.no_direct_answers import NoDirectAnswersRule
from src.policy.rules.tone_by_confidence import ToneByConfidenceRule
from src.policy.types import (
    PolicyContext,
    PolicyDecision,
    QuestionPlan,
    ResponseConstraints,
)

# Module-level singletons — stateless, safe to share across all sessions.
_fsm = PolicyFSM()
_rules: list[BaseRule] = [NoDirectAnswersRule(), ToneByConfidenceRule()]
_interceptors: dict[str, BaseOutputInterceptor] = {
    "direct_answer_detector": DirectAnswerDetectorInterceptor(),
}


class PolicyEngine:
    """
    Stateless orchestrator — safe to share across sessions as a module-level singleton.
    All mutable state is in PolicyContext (caller-owned).
    """

    def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        """
        Evaluate the policy for the current turn.

        Order: FSM transition → question selection → rules applied in order.
        Interceptors are always set to ["direct_answer_detector"] in v1.
        """
        next_state = _fsm.transition(ctx)
        qid, qtext = select_question(next_state, ctx.recent_question_ids)

        plan = QuestionPlan(
            question_id=qid,
            question_text=qtext,
            constraints=ResponseConstraints(),
        )

        applied: list[str] = []
        for rule in _rules:
            result = rule.apply(ctx, plan)
            if result:
                applied.append(result)

        return PolicyDecision(
            next_state=next_state,
            plan=plan,
            applied_rules=applied,
            interceptors=["direct_answer_detector"],
        )

    def check_output(self, raw: str, decision: PolicyDecision) -> tuple[bool, str]:
        """
        Run post-LLM interceptors on the accumulated response.

        If an interceptor fires, it appends the question_text from the plan.
        Returns (was_modified, final_text).
        """
        text = raw
        was_modified = False
        for name in decision.interceptors:
            if interceptor := _interceptors.get(name):
                modified, text = interceptor.process(text, decision.plan.question_text)
                if modified:
                    was_modified = True
        return was_modified, text
