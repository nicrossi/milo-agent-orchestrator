from abc import ABC, abstractmethod

from src.policy.types import PolicyContext, QuestionPlan


class BaseRule(ABC):
    # Phase 4: rules are classified as essential (always run) or non-essential
    # (subject to MetaFeedbackCooldown rate limiting). Essential rules enforce
    # hard guardrails or route the turn (NoDirectAnswers, ElicitAttempt,
    # HintLadder); non-essential rules are cosmetic / meta (ToneByConfidence).
    essential: bool = True

    @abstractmethod
    def apply(self, ctx: PolicyContext, plan: QuestionPlan) -> str | None:
        """Mutate plan in-place. Return rule name if it fired, None otherwise."""
