"""
Score derivation — collapses per-turn UserSignals + a rolling window into a
fixed set of policy-relevant scores in [0.0, 1.0].

The five scores are taken from the assistance-dilemma / SRL literature (see
deep-research-report.md §"Scoring heuristics for signals"):

  struggle:        composite of hedging + confusion + slow latency + low effort
  miscalibration:  confident-sounding but no attempt / asking for answer
  hint_abuse:      repeated direct-answer demands across recent turns
  help_avoidance:  stalled (slow + short reply) but not requesting help
  affect_load:     affective signature of cognitive load (confusion + hedging)

Coefficients are engineering inferences calibrated so that:
  - a single neutral turn produces all-zeros
  - a hedging-heavy turn produces struggle ≈ 0.35–0.5 and affect_load ≈ 0.3–0.4
  - a confused turn produces struggle + affect_load both > 0.4
  - "dame la respuesta" with no attempt + no hedge produces miscalibration = 1.0
"""
from __future__ import annotations

from typing import Sequence

from src.policy.types import Scores, UserSignals

# How many recent turns (including current) feed `hint_abuse`.
_HINT_ABUSE_WINDOW = 3

# Thresholds (tuned for current scaling of latency_z / length_z extractors).
_LATENCY_SLOW_Z = 1.0          # > +1σ on latency means slow
_LENGTH_SHORT_Z = -0.5         # < -0.5σ on length means short
_HEDGING_LOW = 0.1             # below this counts as "confident"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_scores(
    window: Sequence[UserSignals],
    current: UserSignals,
) -> Scores:
    """Compute Scores from the rolling window + current turn signals.

    Args:
        window: prior UserSignals, most recent last. May be empty (cold-start).
        current: signals for the current turn (already built by aggregator).

    Returns:
        Scores with all five fields populated in [0.0, 1.0].
    """
    # ---- struggle ----------------------------------------------------------
    # Latency contribution saturates at +2σ; clamp so very large z doesn't
    # dominate. length_z below -1σ is the "very short" bucket (low effort).
    slow_component = _clamp01(current.latency_z / 2.0) if current.latency_z > 0 else 0.0
    short_component = 1.0 if current.length_z <= -1.0 else 0.0
    struggle = (
        0.35 * current.hedging
        + 0.25 * current.confusion
        + 0.20 * slow_component
        + 0.20 * short_component
    )
    struggle = _clamp01(struggle)

    # ---- miscalibration ----------------------------------------------------
    # Miscalibration requires an explicit behavioral signal that the learner
    # believes they don't need to think (direct-answer demand). Without that,
    # short or vague messages are treated as "ambiguous engagement", not
    # miscalibration — avoids false positives on greetings / lurking.
    #   - confident + no attempt + direct ask  → 1.0 (clearest miscalibration)
    #   - confident + direct ask                → 0.5 (demanding despite some effort)
    #   - otherwise                             → 0.0
    confident_proxy = current.hedging < _HEDGING_LOW
    if confident_proxy and current.direct_answer_request and not current.attempt_present:
        miscalibration = 1.0
    elif confident_proxy and current.direct_answer_request:
        miscalibration = 0.5
    else:
        miscalibration = 0.0

    # ---- hint_abuse --------------------------------------------------------
    # Count direct-answer requests in the last N turns (incl. current).
    recent: list[UserSignals] = list(window[-(_HINT_ABUSE_WINDOW - 1):]) + [current]
    direct_asks = sum(1 for s in recent if s.direct_answer_request)
    if direct_asks < 2:
        hint_abuse = 0.0
    else:
        hint_abuse = _clamp01(direct_asks / float(_HINT_ABUSE_WINDOW))

    # ---- help_avoidance ----------------------------------------------------
    # Slow + short + not requesting help => stalled silently.
    slow = current.latency_z > _LATENCY_SLOW_Z
    short = current.length_z < _LENGTH_SHORT_Z
    not_asking = not current.direct_answer_request
    help_avoidance = 1.0 if (slow and short and not_asking) else 0.0

    # ---- affect_load -------------------------------------------------------
    # Affective signature: confusion-dominant + hedging-flavored.
    affect_load = _clamp01(0.6 * current.confusion + 0.4 * current.hedging)

    return Scores(
        struggle=struggle,
        miscalibration=miscalibration,
        hint_abuse=hint_abuse,
        help_avoidance=help_avoidance,
        affect_load=affect_load,
    )
