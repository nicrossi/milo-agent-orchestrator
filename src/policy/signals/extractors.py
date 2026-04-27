"""
Pure signal extractors — text- and timing-based heuristics that derive learner-state
signals from a single turn. Each function is a pure function with no side effects;
all return numeric or boolean values.

Lexicons are bilingual (Spanish + English). All matching is case-insensitive and
operates on token boundaries to avoid substring false positives.

Design notes:
- Extractors return floats in [0.0, 1.0] for graded signals (hedging, confusion).
  Boolean signals (attempt_present, direct_answer_request) return bool.
- z-score extractors (length, latency) return floats centered on 0; positive means
  "above the rolling baseline." When window length < 3 they return 0.0 (neutral).
- Lexicons are deliberately small and curated; expansion is a Phase 6 / future task.
"""
from __future__ import annotations

import math
import re
import statistics
from typing import Sequence

# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------

_HEDGING_PATTERNS = [
    # Spanish
    r"\bcreo\s+que\b",
    r"\bme\s+parece\b",
    r"\btal\s+vez\b",
    r"\bquiz[aá]s?\b",
    r"\bno\s+s[eé]\b",
    r"\bno\s+estoy\s+seguro\b",
    r"\bno\s+estoy\s+segura\b",
    r"\ba\s+lo\s+mejor\b",
    r"\bcapaz\b",
    # English
    r"\bi\s+think\b",
    r"\bmaybe\b",
    r"\bnot\s+sure\b",
    r"\bperhaps\b",
    r"\bi\s+guess\b",
    r"\bkind\s+of\b",
    r"\bsort\s+of\b",
]

_CONFUSION_PATTERNS = [
    # Spanish
    r"\bno\s+entiendo\b",
    r"\bno\s+lo\s+entiendo\b",
    r"\bno\s+comprendo\b",
    r"\bestoy\s+perdido\b",
    r"\bestoy\s+perdida\b",
    r"\bme\s+perd[ií]\b",
    r"\bno\s+tiene\s+sentido\b",
    r"\bme\s+confunde\b",
    r"\bestoy\s+confundido\b",
    r"\bestoy\s+confundida\b",
    # English
    r"\bi'?m\s+lost\b",
    r"\bi'?m\s+confused\b",
    r"\bdoesn'?t\s+make\s+sense\b",
    r"\bi\s+don'?t\s+understand\b",
    r"\bi\s+don'?t\s+get\s+it\b",
]

_DIRECT_ANSWER_PATTERNS = [
    # Spanish
    r"\bdame\s+la\s+respuesta\b",
    r"\bdime\s+la\s+respuesta\b",
    r"\bdec[ií]me\s+la\s+respuesta\b",
    r"\bcu[aá]l\s+es\s+la\s+respuesta\b",
    r"\bnecesito\s+la\s+respuesta\b",
    r"\bdame\s+el\s+resultado\b",
    r"\bdime\s+el\s+resultado\b",
    r"\bc[oó]mo\s+se\s+hace\b",
    r"\bdec[ií]me\s+c[oó]mo\b",
    r"\bexpl[ií]came\b",
    r"\bcontame\s+la\s+respuesta\b",
    # English
    r"\bgive\s+me\s+the\s+answer\b",
    r"\btell\s+me\s+the\s+answer\b",
    r"\bwhat\s+is\s+the\s+answer\b",
    r"\bjust\s+tell\s+me\b",
    r"\bgive\s+me\s+the\s+result\b",
    r"\bhow\s+do\s+you\s+do\b",
]

# Tokens that suggest the learner is presenting an attempt rather than just
# asking for help. Reasoning markers, conditional structures, justifications.
_ATTEMPT_MARKERS = [
    # Spanish
    r"\bporque\b",
    r"\bya\s+que\b",
    r"\bdebido\s+a\b",
    r"\bentonces\b",
    r"\bpor\s+lo\s+tanto\b",
    r"\bme\s+parece\s+que\b",
    r"\bcreo\s+que\b",
    r"\bsi\s+.*\s+entonces\b",
    r"\bla\s+raz[oó]n\b",
    r"\bmi\s+idea\b",
    r"\bprob[eé]\b",
    r"\bintent[eé]\b",
    # English
    r"\bbecause\b",
    r"\bsince\b",
    r"\btherefore\b",
    r"\bso\b",
    r"\bi\s+think\s+that\b",
    r"\bif\s+.*\s+then\b",
    r"\bmy\s+idea\b",
    r"\bi\s+tried\b",
]

_REVISION_MARKERS = [
    # Spanish
    r"\bperd[oó]n\b",
    r"\bperdona\b",
    r"\bquise\s+decir\b",
    r"\bme\s+equivoqu[eé]\b",
    r"\ben\s+realidad\b",
    r"\bmejor\s+dicho\b",
    r"\bah\s+no\b",
    r"\bahora\s+que\s+lo\s+pienso\b",
    # English
    r"\bsorry\b",
    r"\bactually\b",
    r"\bi\s+meant\b",
    r"\bwait\b",
    r"\bnever\s+mind\b",
    r"\bon\s+second\s+thought\b",
]

# Pre-compile all patterns once at import.
_HEDGING_RE = [re.compile(p, re.IGNORECASE) for p in _HEDGING_PATTERNS]
_CONFUSION_RE = [re.compile(p, re.IGNORECASE) for p in _CONFUSION_PATTERNS]
_DIRECT_ANSWER_RE = [re.compile(p, re.IGNORECASE) for p in _DIRECT_ANSWER_PATTERNS]
_ATTEMPT_RE = [re.compile(p, re.IGNORECASE) for p in _ATTEMPT_MARKERS]
_REVISION_RE = [re.compile(p, re.IGNORECASE) for p in _REVISION_MARKERS]

# Saturation cap: if a 5-word message has 2 hedges, hedging score ≈ 1.0.
_HEDGING_SATURATION_RATIO = 0.40
_CONFUSION_SATURATION_RATIO = 0.30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def _count_pattern_hits(text: str, patterns: list[re.Pattern]) -> int:
    return sum(1 for p in patterns if p.search(text))


def _z_score(value: float, window: Sequence[float]) -> float:
    """Compute z-score of `value` against `window`.

    Returns 0.0 when window is too short (<3 samples) or has zero variance,
    so callers don't get spurious z-scores from cold-start sessions.
    """
    if len(window) < 3:
        return 0.0
    try:
        mean = statistics.mean(window)
        stdev = statistics.stdev(window)
    except statistics.StatisticsError:
        return 0.0
    if stdev == 0.0 or math.isnan(stdev):
        return 0.0
    return (value - mean) / stdev


# ---------------------------------------------------------------------------
# Public extractors
# ---------------------------------------------------------------------------

def extract_hedging(text: str) -> float:
    """Return [0.0, 1.0] score of hedging-language density in the message."""
    if not text or not text.strip():
        return 0.0
    hits = _count_pattern_hits(text, _HEDGING_RE)
    if hits == 0:
        return 0.0
    words = max(_word_count(text), 1)
    # Saturate: a single hedge in a short message scores high; many hedges saturates at 1.0.
    raw = hits / max(words * _HEDGING_SATURATION_RATIO, 1.0)
    return min(1.0, raw)


def extract_confusion_keywords(text: str) -> float:
    """Return [0.0, 1.0] score of confusion-keyword density."""
    if not text or not text.strip():
        return 0.0
    hits = _count_pattern_hits(text, _CONFUSION_RE)
    if hits == 0:
        return 0.0
    words = max(_word_count(text), 1)
    raw = hits / max(words * _CONFUSION_SATURATION_RATIO, 1.0)
    return min(1.0, raw)


def extract_direct_answer_request(text: str) -> bool:
    """True when the message contains an explicit ask for the answer."""
    if not text or not text.strip():
        return False
    return _count_pattern_hits(text, _DIRECT_ANSWER_RE) > 0


def extract_attempt_presence(text: str) -> bool:
    """
    True when the message looks like a learner attempt (contains reasoning markers,
    is non-trivial in length) and is NOT primarily a direct-answer request.

    Returns True for the empty string (treated as "no claim about lack of attempt"
    so the empty/greeting turn doesn't trip elicitation rules).
    """
    if not text or not text.strip():
        return True

    # If the message is dominated by a direct-answer request, it's not an attempt.
    direct_ask = extract_direct_answer_request(text)
    has_marker = _count_pattern_hits(text, _ATTEMPT_RE) > 0
    words = _word_count(text)

    if direct_ask and not has_marker:
        return False

    # A short message with no attempt markers and no clear content is not an attempt.
    if words < 4 and not has_marker:
        return False

    return True


def extract_revision_markers(text: str) -> int:
    """Count of self-correction / revision markers in the message."""
    if not text or not text.strip():
        return 0
    return _count_pattern_hits(text, _REVISION_RE)


def extract_message_length_z(text: str, length_window: Sequence[int]) -> float:
    """Z-score of current message length (in words) vs the rolling window.

    Negative means shorter than baseline (potential disengagement).
    Positive means longer (potentially elaborative).
    """
    current = _word_count(text)
    return _z_score(float(current), [float(n) for n in length_window])


def extract_latency_z(latency_seconds: float, latency_window: Sequence[float]) -> float:
    """Z-score of current turn latency vs the rolling window.

    Positive means slower than baseline (struggle / deliberation).
    Returns 0.0 when latency_seconds is negative (e.g. server-side first turn).
    """
    if latency_seconds is None or latency_seconds < 0:
        return 0.0
    return _z_score(latency_seconds, latency_window)
