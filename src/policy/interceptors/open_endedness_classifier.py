"""
Open-endedness classifier — rule-based scorer that estimates whether a
sentence functions as an open, generative question vs a closed/rhetorical one.

Used by:
  - DirectAnswerDetectorInterceptor: a response with no open question is a
    direct answer, regardless of whether a "?" appears.
  - RhetoricalQuestionDetectorInterceptor: distinguishes substantive question
    from rhetorical filler at the end of an assertion.

Heuristics (not ML — deliberately deterministic and inspectable):
  - Wh-stems in Spanish/English ("¿qué", "¿cómo", "¿por qué", "what", "how"…)
    score high (open-ended).
  - Closed-form patterns ("¿sí?", "¿verdad?", "¿no?", "¿ok?", "¿entendiste?")
    score very low (rhetorical / yes-no).
  - Plain interrogative without wh-stem and without closed marker scores in
    the middle (treated as ambiguous-leaning-closed).
  - Sentences without a "?" score 0.0 (not a question at all).
"""
from __future__ import annotations

import re

# Open / generative question stems. Hits anywhere in the sentence (not just
# at the start) so embedded clauses still register.
_OPEN_STEM_RE = re.compile(
    r"(?:"
    # Spanish wh-stems
    r"\bqu[eé]\b|\bcu[aá]l(?:es)?\b|\bc[oó]mo\b|\bpor\s+qu[eé]\b|\bpara\s+qu[eé]\b|"
    r"\bd[oó]nde\b|\bcu[aá]ndo\b|\bqui[eé]n(?:es)?\b|\bcu[aá]nto\b|"
    # English wh-stems
    r"\bwhat\b|\bwhy\b|\bhow\b|\bwhich\b|\bwhere\b|\bwhen\b|\bwho\b"
    r")",
    re.IGNORECASE,
)

# Closed / rhetorical patterns. Match the SENTENCE (after stripping leading
# Spanish "¿" and trailing "?") to be robust to punctuation variants.
_CLOSED_PATTERNS = [
    r"^(s[ií])$",
    r"^(no)$",
    r"^(verdad)$",
    r"^(cierto)$",
    r"^(ok)$",
    r"^(okay)$",
    r"^(entendiste)$",
    r"^(entendi(?:ste|eron)?)$",
    r"^(est[aá]\s+claro)$",
    r"^(tiene\s+sentido)$",
    r"^(de\s+acuerdo)$",
    # English
    r"^(right)$",
    r"^(ok(?:ay)?)$",
    r"^(got\s+it)$",
    r"^(make\s+sense)$",
    r"^(does\s+that\s+make\s+sense)$",
    r"^(do\s+you\s+understand)$",
    r"^(you\s+see)$",
]
_CLOSED_RE = [re.compile(p, re.IGNORECASE) for p in _CLOSED_PATTERNS]


def open_endedness_score(sentence: str) -> float:
    """Return a score in [0.0, 1.0] for how open-ended a sentence is.

    0.0 = not a question at all, or definitively closed/rhetorical.
    ~0.3 = interrogative but no open stem (yes/no leaning).
    ~0.7 = wh-stem present (open generative question).
    """
    if not sentence or "?" not in sentence:
        return 0.0

    # Normalize for closed-pattern check: strip Spanish opening/closing marks
    # and surrounding whitespace.
    core = sentence.strip().strip("¿?¡!.,;:").strip()
    for cre in _CLOSED_RE:
        if cre.match(core):
            return 0.1

    # Open stem present?
    if _OPEN_STEM_RE.search(sentence):
        return 0.7

    # Has "?" but no open stem and not a known closed pattern → mid (yes/no).
    return 0.3


def split_sentences(text: str) -> list[str]:
    """Split text into sentences for per-sentence open-endedness scoring.

    Splits on "?", "!", "." while keeping the terminator with the sentence.
    Does not handle decimals or abbreviations — sufficient for chat output
    where Milo's responses are short and conversational.
    """
    if not text or not text.strip():
        return []
    # Split keeping delimiters by capturing them.
    parts = re.split(r"(?<=[\?\!\.])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]
