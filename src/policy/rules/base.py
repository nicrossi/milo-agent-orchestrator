from abc import ABC, abstractmethod

from src.policy.types import PolicyContext, QuestionPlan


class BaseRule(ABC):
    @abstractmethod
    def apply(self, ctx: PolicyContext, plan: QuestionPlan) -> str | None:
        """Mutate plan in-place. Return rule name if it fired, None otherwise."""
