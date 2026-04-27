"""
RhetoricalQuestionDetectorInterceptor — closes a robustness hole in the
output guardrail.

The bare DirectAnswerDetector is bypassed when the LLM ends an assertive
response with a closed-form rhetorical question:

    "Los herbívoros mueren de hambre. ¿Entendiste?"

That output technically contains "?" but it's a yes/no rhetorical filler,
not a Socratic question. This interceptor catches that pattern and appends
the planned Socratic question.

Detection (all must hold):
  1. The LLM output contains at least one "?".
  2. The first sentence (or the assertive prefix before the first "?") is
     ≥ 8 words long — i.e. the response is mostly assertion, not question.
  3. NO sentence in the output scores as open-ended (open_endedness_score
     ≥ 0.5). All "?"s are closed/rhetorical.

When all three are true, the interceptor appends "\n\n{question_text}" to
the output. Otherwise pass-through.
"""
from __future__ import annotations

from src.policy.interceptors.base import BaseOutputInterceptor
from src.policy.interceptors.open_endedness_classifier import (
    open_endedness_score,
    split_sentences,
)

__evidence__ = ["graesser_person_1994_question_quality"]

_ASSERTIVE_PREFIX_MIN_WORDS = 8
_OPEN_ENDED_THRESHOLD = 0.5


class RhetoricalQuestionDetectorInterceptor(BaseOutputInterceptor):
    name = "rhetorical_question_detector"

    def process(self, llm_output: str, question_text: str) -> tuple[bool, str]:
        if not llm_output or "?" not in llm_output:
            # If there's no "?" at all, this isn't our concern — the
            # DirectAnswerDetector handles "no question present" cases.
            return False, llm_output

        sentences = split_sentences(llm_output)
        if not sentences:
            return False, llm_output

        # If ANY sentence is open-ended enough, no rhetorical violation.
        if any(open_endedness_score(s) >= _OPEN_ENDED_THRESHOLD for s in sentences):
            return False, llm_output

        # All "?"s are closed/rhetorical. Now check that the response is
        # mostly assertion (not just a string of yes/no questions).
        # Use the prefix up to the first "?" as the assertive part.
        first_q_idx = llm_output.find("?")
        prefix = llm_output[:first_q_idx]
        prefix_words = len([w for w in prefix.split() if w.strip()])

        if prefix_words < _ASSERTIVE_PREFIX_MIN_WORDS:
            # Too short to be an assertion-then-rhetoric pattern.
            return False, llm_output

        corrected = llm_output.rstrip() + "\n\n" + question_text
        return True, corrected
