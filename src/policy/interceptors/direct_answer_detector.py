"""
DirectAnswerDetectorInterceptor — post-processes LLM output to detect direct
answers and append a Socratic question when violation is found.

Detection (any one triggers a violation):
  (a) NO sentence in the output scores as open-ended (>= 0.5). The presence
      of "?" alone is no longer enough — closed-form "¿Entendiste?" rhetorical
      questions are caught here. The RhetoricalQuestionDetector handles the
      narrower "long assertion + short rhetorical" case for better diagnostics.
  (b) The output starts with a known direct-answer prefix pattern
      (e.g. "La respuesta es", "The answer is", "El resultado es").

On violation: appends "\\n\\n{question_text}" and returns (True, corrected).
On clean output: returns (False, original_text) unchanged.
"""
import re

from src.policy.interceptors.base import BaseOutputInterceptor
from src.policy.interceptors.open_endedness_classifier import (
    open_endedness_score,
    split_sentences,
)

__evidence__ = ["narciss_2008_informative_tutoring_feedback"]

_DIRECT_ANSWER_PREFIXES = re.compile(
    r"^\s*("
    r"la\s+respuesta\s+es"
    r"|el\s+resultado\s+es"
    r"|the\s+answer\s+is"
    r"|the\s+result\s+is"
    r"|la\s+solución\s+es"
    r"|la\s+solucion\s+es"
    r")",
    re.IGNORECASE,
)

_OPEN_ENDED_THRESHOLD = 0.5


class DirectAnswerDetectorInterceptor(BaseOutputInterceptor):
    name = "direct_answer_detector"

    def process(self, llm_output: str, question_text: str) -> tuple[bool, str]:
        if not llm_output:
            # Empty output: nothing useful to send. Replace with the planned
            # question so the user gets something to respond to.
            return True, question_text

        starts_with_direct = bool(_DIRECT_ANSWER_PREFIXES.match(llm_output))

        sentences = split_sentences(llm_output)
        has_open_question = any(
            open_endedness_score(s) >= _OPEN_ENDED_THRESHOLD for s in sentences
        )

        if has_open_question and not starts_with_direct:
            return False, llm_output

        corrected = llm_output.rstrip() + "\n\n" + question_text
        return True, corrected
