"""
DirectAnswerDetectorInterceptor — post-processes LLM output to detect direct answers.

Detection heuristics (either triggers a violation):
  (a) The output contains no "?" — Milo must always ask a reflective question.
  (b) The output starts with a known direct-answer prefix pattern (e.g. "La respuesta es",
      "The answer is", "El resultado es").

On violation: appends "\\n\\n{question_text}" to the output and returns (True, corrected_text).
On clean output: returns (False, original_text) unchanged.
"""
import re

from src.policy.interceptors.base import BaseOutputInterceptor

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


class DirectAnswerDetectorInterceptor(BaseOutputInterceptor):
    name = "direct_answer_detector"

    def process(self, llm_output: str, question_text: str) -> tuple[bool, str]:
        has_question = "?" in llm_output
        starts_with_direct = bool(_DIRECT_ANSWER_PREFIXES.match(llm_output))

        if has_question and not starts_with_direct:
            return False, llm_output

        corrected = llm_output + "\n\n" + question_text
        return True, corrected
