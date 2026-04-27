"""Tests for src/policy/cooldown.py — meta-feedback rate limiting."""
from src.policy.cooldown import MetaFeedbackCooldown


def test_allows_when_counter_at_default_high():
    cd = MetaFeedbackCooldown(99)
    assert cd.allows_intervention() is True


def test_allows_at_threshold():
    # Threshold is 2 → counter == 2 means "we've waited 2 turns" → allow.
    cd = MetaFeedbackCooldown(2)
    assert cd.allows_intervention() is True


def test_suppresses_just_below_threshold():
    cd = MetaFeedbackCooldown(1)
    assert cd.allows_intervention() is False


def test_suppresses_immediately_after_firing():
    cd = MetaFeedbackCooldown(0)
    assert cd.allows_intervention() is False


def test_compute_next_resets_when_fired():
    cd = MetaFeedbackCooldown(5)
    assert cd.compute_next(any_non_essential_fired=True) == 0


def test_compute_next_increments_when_not_fired():
    cd = MetaFeedbackCooldown(3)
    assert cd.compute_next(any_non_essential_fired=False) == 4


def test_compute_next_caps_at_high_value():
    cd = MetaFeedbackCooldown(999)
    # Doesn't grow unboundedly.
    assert cd.compute_next(any_non_essential_fired=False) == 999
