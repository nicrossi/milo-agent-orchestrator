"""Tests for src/policy/signals/aggregator.py — orchestrating extractors."""
from src.policy.signals.aggregator import build_user_signals, message_word_count
from src.policy.types import UserSignals


def test_aggregator_neutral_message_neutral_signals():
    sig = build_user_signals(
        user_message="hola, vamos a empezar",
        signals_window=[],
        prev_milo_response_ts=None,
        now_ts=1000.0,
    )
    assert sig.hedging == 0.0
    assert sig.confusion == 0.0
    assert sig.direct_answer_request is False
    assert sig.attempt_present is True  # length-based, not flagged as non-attempt
    assert sig.latency_z == 0.0
    assert sig.length_z == 0.0
    assert sig.revisions == 0


def test_aggregator_hedging_message():
    sig = build_user_signals(
        user_message="creo que tal vez no sé bien la respuesta",
        signals_window=[],
        prev_milo_response_ts=None,
        now_ts=1000.0,
    )
    assert sig.hedging > 0.5


def test_aggregator_direct_answer_request_no_attempt():
    sig = build_user_signals(
        user_message="dame la respuesta",
        signals_window=[],
        prev_milo_response_ts=None,
        now_ts=1000.0,
    )
    assert sig.direct_answer_request is True
    assert sig.attempt_present is False


def test_aggregator_latency_z_uses_window():
    sig = build_user_signals(
        user_message="ok",
        signals_window=[],
        prev_milo_response_ts=900.0,
        now_ts=1000.0,
        latency_window=[5.0, 6.0, 5.5, 6.2, 5.8],
    )
    # 100s vs ~5s baseline => very high z-score
    assert sig.latency_z > 1.5


def test_aggregator_latency_zero_when_no_prev_response():
    sig = build_user_signals(
        user_message="ok",
        signals_window=[],
        prev_milo_response_ts=None,
        now_ts=1000.0,
        latency_window=[5.0, 6.0, 5.5],
    )
    # No prev_milo_response_ts => latency reading invalid => z = 0.0
    assert sig.latency_z == 0.0


def test_aggregator_length_z_uses_window():
    long_text = " ".join(["palabra"] * 30)
    sig = build_user_signals(
        user_message=long_text,
        signals_window=[],
        prev_milo_response_ts=None,
        now_ts=1000.0,
        length_window=[3, 4, 5, 4, 3],
    )
    assert sig.length_z > 1.5


def test_aggregator_returns_default_user_signals_shape():
    """Phase 6 invariant: UserSignals has the expected fields, no `confidence`."""
    sig = build_user_signals(
        user_message="anything",
        signals_window=[],
        prev_milo_response_ts=None,
        now_ts=1000.0,
    )
    # All extractor-driven fields populated
    for field in (
        "hedging", "confusion", "attempt_present", "direct_answer_request",
        "latency_z", "length_z", "revisions",
    ):
        assert hasattr(sig, field)
    # The legacy `confidence` field was removed in Phase 6.
    assert not hasattr(sig, "confidence")


def test_message_word_count_helper():
    assert message_word_count("") == 0
    assert message_word_count("   ") == 0
    assert message_word_count("hola mundo") == 2
    assert message_word_count("uno  dos   tres") == 3
