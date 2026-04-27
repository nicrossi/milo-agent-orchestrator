"""Tests for src/policy/scores.py — score derivation from UserSignals + window."""
from src.policy.scores import compute_scores
from src.policy.types import UserSignals


def neutral() -> UserSignals:
    return UserSignals()  # all defaults


# --- struggle ---

def test_neutral_signals_produce_zero_struggle():
    scores = compute_scores([], neutral())
    assert scores.struggle == 0.0


def test_hedging_alone_raises_struggle():
    sig = UserSignals(hedging=1.0)
    scores = compute_scores([], sig)
    assert scores.struggle >= 0.30


def test_confusion_alone_raises_struggle():
    sig = UserSignals(confusion=1.0)
    scores = compute_scores([], sig)
    assert scores.struggle >= 0.20


def test_slow_latency_contributes_to_struggle():
    sig = UserSignals(latency_z=2.0)  # +2σ saturates the latency component
    scores = compute_scores([], sig)
    assert scores.struggle >= 0.15


def test_combined_signals_compound_struggle():
    sig = UserSignals(hedging=0.8, confusion=0.6, latency_z=2.0, length_z=-1.5)
    scores = compute_scores([], sig)
    # 0.35*0.8 + 0.25*0.6 + 0.20*1.0 + 0.20*1.0 = 0.83
    assert scores.struggle > 0.7


def test_struggle_clamped_at_one():
    sig = UserSignals(hedging=1.0, confusion=1.0, latency_z=5.0, length_z=-3.0)
    scores = compute_scores([], sig)
    assert scores.struggle == 1.0


# --- miscalibration ---

def test_miscalibration_zero_on_neutral():
    scores = compute_scores([], neutral())
    assert scores.miscalibration == 0.0


def test_miscalibration_max_on_confident_no_attempt_asking():
    sig = UserSignals(
        hedging=0.0,
        attempt_present=False,
        direct_answer_request=True,
    )
    scores = compute_scores([], sig)
    assert scores.miscalibration == 1.0


def test_miscalibration_zero_without_explicit_ask():
    # Confident-sounding + no attempt but no direct ask = ambiguous
    # engagement (e.g. greeting / lurking), NOT miscalibration.
    sig = UserSignals(
        hedging=0.0,
        attempt_present=False,
        direct_answer_request=False,
    )
    scores = compute_scores([], sig)
    assert scores.miscalibration == 0.0


def test_miscalibration_partial_when_demanding_despite_effort():
    # The learner did make an attempt, but is now demanding the answer
    # without hedging — partial miscalibration signal.
    sig = UserSignals(
        hedging=0.0,
        attempt_present=True,
        direct_answer_request=True,
    )
    scores = compute_scores([], sig)
    assert scores.miscalibration == 0.5


def test_miscalibration_zero_when_hedging_present():
    sig = UserSignals(
        hedging=0.5,  # not confident
        attempt_present=False,
        direct_answer_request=True,
    )
    scores = compute_scores([], sig)
    assert scores.miscalibration == 0.0


# --- hint_abuse ---

def test_hint_abuse_zero_on_single_request():
    sig = UserSignals(direct_answer_request=True)
    scores = compute_scores([], sig)
    assert scores.hint_abuse == 0.0


def test_hint_abuse_rises_on_repeated_requests():
    history = [
        UserSignals(direct_answer_request=True),
        UserSignals(direct_answer_request=True),
    ]
    current = UserSignals(direct_answer_request=True)
    scores = compute_scores(history, current)
    # 3 hits in window of 3 => 1.0
    assert scores.hint_abuse == 1.0


def test_hint_abuse_partial_on_two_of_three():
    history = [
        UserSignals(direct_answer_request=True),
        UserSignals(direct_answer_request=False),
    ]
    current = UserSignals(direct_answer_request=True)
    scores = compute_scores(history, current)
    # 2 hits in window of 3 => 2/3 ≈ 0.67
    assert 0.5 < scores.hint_abuse < 0.8


# --- help_avoidance ---

def test_help_avoidance_zero_on_neutral():
    assert compute_scores([], neutral()).help_avoidance == 0.0


def test_help_avoidance_fires_on_slow_short_silent():
    sig = UserSignals(
        latency_z=2.0,           # slow
        length_z=-1.0,           # short
        direct_answer_request=False,  # not asking
    )
    scores = compute_scores([], sig)
    assert scores.help_avoidance == 1.0


def test_help_avoidance_zero_when_asking_for_help():
    sig = UserSignals(
        latency_z=2.0,
        length_z=-1.0,
        direct_answer_request=True,  # they ARE asking
    )
    scores = compute_scores([], sig)
    assert scores.help_avoidance == 0.0


# --- affect_load ---

def test_affect_load_zero_on_neutral():
    assert compute_scores([], neutral()).affect_load == 0.0


def test_affect_load_dominated_by_confusion():
    sig = UserSignals(confusion=1.0, hedging=0.0)
    scores = compute_scores([], sig)
    assert scores.affect_load == 0.6


def test_affect_load_combines_confusion_and_hedging():
    sig = UserSignals(confusion=1.0, hedging=1.0)
    scores = compute_scores([], sig)
    assert scores.affect_load == 1.0


def test_affect_load_clamped_at_one():
    sig = UserSignals(confusion=1.0, hedging=1.0)
    scores = compute_scores([], sig)
    assert scores.affect_load <= 1.0


# --- monotonicity / regression invariants ---

def test_struggle_monotonic_with_hedging():
    base = UserSignals(hedging=0.0)
    higher = UserSignals(hedging=0.5)
    highest = UserSignals(hedging=1.0)
    s_base = compute_scores([], base).struggle
    s_higher = compute_scores([], higher).struggle
    s_highest = compute_scores([], highest).struggle
    assert s_base < s_higher < s_highest


def test_all_scores_in_unit_interval():
    extreme = UserSignals(
        hedging=1.0,
        confusion=1.0,
        attempt_present=False,
        direct_answer_request=True,
        latency_z=5.0,
        length_z=-5.0,
        revisions=10,
    )
    history = [extreme, extreme, extreme]
    scores = compute_scores(history, extreme)
    for field in ("struggle", "miscalibration", "hint_abuse", "help_avoidance", "affect_load"):
        v = getattr(scores, field)
        assert 0.0 <= v <= 1.0, f"{field}={v} out of [0,1]"
