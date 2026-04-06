"""
NoDirectAnswersRule — fires when the user explicitly requests a direct answer.

Trigger phrases (Spanish and English):
  Spanish: "dame la respuesta", "dime la respuesta", "cuál es la respuesta",
           "cual es la respuesta", "decime la respuesta", "necesito la respuesta",
           "dame el resultado", "dime el resultado", "cómo se hace", "como se hace",
           "decime cómo", "explicame", "explícame"
  English: "give me the answer", "tell me the answer", "what is the answer",
           "just tell me", "give me the result", "how do you do"
"""
import re

from src.policy.rules.base import BaseRule
from src.policy.types import PolicyContext, QuestionPlan

_TRIGGER_PATTERNS = [
    # Spanish
    r"dame\s+la\s+respuesta",
    r"dime\s+la\s+respuesta",
    r"cu[aá]l\s+es\s+la\s+respuesta",
    r"decime\s+la\s+respuesta",
    r"necesito\s+la\s+respuesta",
    r"dame\s+el\s+resultado",
    r"dime\s+el\s+resultado",
    r"c[oó]mo\s+se\s+hace",
    r"decime\s+c[oó]mo",
    r"expl[ií]came",
    # English
    r"give\s+me\s+the\s+answer",
    r"tell\s+me\s+the\s+answer",
    r"what\s+is\s+the\s+answer",
    r"just\s+tell\s+me",
    r"give\s+me\s+the\s+result",
    r"how\s+do\s+you\s+do",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _TRIGGER_PATTERNS]


class NoDirectAnswersRule(BaseRule):
    def apply(self, ctx: PolicyContext, plan: QuestionPlan) -> str | None:
        msg = ctx.user_message
        if not msg:
            return None
        for pattern in _COMPILED:
            if pattern.search(msg):
                plan.constraints.forbid_direct_answer = True
                plan.constraints.must_ask_question = True
                plan.prompt_directives.append(
                    "IMPORTANT: Do NOT give a direct answer. "
                    "Instead, respond only with a reflective question that guides the student to find the answer themselves."
                )
                return "no_direct_answers"
        return None
