"""Tests for src/policy/signals/extractors.py — pure heuristic functions."""
from src.policy.signals.extractors import (
    extract_attempt_presence,
    extract_confusion_keywords,
    extract_direct_answer_request,
    extract_hedging,
    extract_latency_z,
    extract_message_length_z,
    extract_revision_markers,
)


# --- hedging ---

def test_hedging_zero_on_neutral_text():
    assert extract_hedging("la respuesta es 42") == 0.0


def test_hedging_detects_spanish():
    score = extract_hedging("creo que tal vez no sé bien")
    assert 0.5 < score <= 1.0


def test_hedging_detects_english():
    score = extract_hedging("I think maybe I'm not sure")
    assert 0.4 < score <= 1.0


def test_hedging_zero_on_empty():
    assert extract_hedging("") == 0.0
    assert extract_hedging("   ") == 0.0


def test_hedging_saturates_at_one():
    text = "creo que tal vez no sé quizás capaz a lo mejor"
    assert extract_hedging(text) == 1.0


# --- confusion ---

def test_confusion_zero_on_neutral():
    assert extract_confusion_keywords("entendí, gracias") == 0.0


def test_confusion_detects_spanish():
    assert extract_confusion_keywords("no entiendo nada de esto") > 0.3


def test_confusion_detects_english():
    assert extract_confusion_keywords("I'm lost, this doesn't make sense") > 0.3


def test_confusion_zero_on_empty():
    assert extract_confusion_keywords("") == 0.0


# --- direct answer request ---

def test_direct_answer_request_spanish():
    assert extract_direct_answer_request("dame la respuesta") is True
    assert extract_direct_answer_request("decime cómo se hace") is True
    assert extract_direct_answer_request("explícame") is True


def test_direct_answer_request_english():
    assert extract_direct_answer_request("just tell me") is True
    assert extract_direct_answer_request("give me the answer please") is True


def test_direct_answer_request_false_on_neutral():
    assert extract_direct_answer_request("estoy pensando en cómo seguir") is False


def test_direct_answer_request_false_on_empty():
    assert extract_direct_answer_request("") is False


# --- attempt presence ---

def test_attempt_presence_true_on_reasoning():
    assert extract_attempt_presence(
        "creo que la respuesta es X porque la regla dice Y"
    ) is True


def test_attempt_presence_true_on_english_reasoning():
    assert extract_attempt_presence("I think it's X because of Y") is True


def test_attempt_presence_false_on_pure_direct_answer_request():
    assert extract_attempt_presence("dame la respuesta") is False
    assert extract_attempt_presence("just tell me") is False


def test_attempt_presence_false_on_short_no_content():
    assert extract_attempt_presence("ok") is False
    assert extract_attempt_presence("no sé") is False


def test_attempt_presence_true_on_empty_treated_as_neutral():
    # Empty/greeting turns shouldn't trip elicitation rules.
    assert extract_attempt_presence("") is True


def test_attempt_presence_true_when_long_enough_without_markers():
    # Substantive engagement without explicit markers — still counts as attempt.
    assert extract_attempt_presence("estoy explorando esta idea sobre el ecosistema") is True


# --- revisions ---

def test_revisions_zero_on_neutral():
    assert extract_revision_markers("la respuesta es X") == 0


def test_revisions_counts_spanish():
    assert extract_revision_markers("perdón, en realidad quise decir Y") >= 2


def test_revisions_counts_english():
    assert extract_revision_markers("wait, actually I meant Y") >= 2


# --- length z-score ---

def test_length_z_zero_on_short_window():
    assert extract_message_length_z("hola mundo", []) == 0.0
    assert extract_message_length_z("hola mundo", [5, 6]) == 0.0


def test_length_z_positive_on_above_baseline():
    # Window of short messages, current message much longer
    window = [3, 4, 4, 3, 5]
    long_text = " ".join(["palabra"] * 20)
    assert extract_message_length_z(long_text, window) > 1.0


def test_length_z_negative_on_below_baseline():
    window = [20, 22, 18, 21, 19]
    short_text = "hola"
    assert extract_message_length_z(short_text, window) < -1.0


def test_length_z_zero_on_zero_variance_window():
    # Identical lengths in window => stdev == 0 => return 0.0
    assert extract_message_length_z("text here", [4, 4, 4, 4]) == 0.0


# --- latency z-score ---

def test_latency_z_zero_on_short_window():
    assert extract_latency_z(10.0, []) == 0.0
    assert extract_latency_z(10.0, [5.0, 6.0]) == 0.0


def test_latency_z_positive_when_slow():
    window = [3.0, 4.0, 3.5, 4.5, 3.8]
    assert extract_latency_z(20.0, window) > 1.5


def test_latency_z_negative_when_fast():
    window = [10.0, 12.0, 11.5, 10.5, 11.0]
    assert extract_latency_z(2.0, window) < -1.0


def test_latency_z_zero_on_negative_input():
    # Server-side first turn or invalid measurement
    assert extract_latency_z(-1.0, [5.0, 6.0, 7.0]) == 0.0
