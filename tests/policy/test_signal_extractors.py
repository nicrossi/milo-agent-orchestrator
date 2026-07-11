"""Tests for src/policy/signals/extractors.py — pure heuristic functions."""
from src.policy.signals.extractors import (
    extract_attempt_presence,
    extract_confusion_keywords,
    extract_direct_answer_request,
    extract_done_signal,
    extract_hedging,
    extract_latency_z,
    extract_message_length_z,
    extract_procedural_request,
    extract_revision_markers,
)


# --- done_signal ---

def test_done_signal_detects_ya_hicimos():
    # From the ecosystem transcript: "lo cual eso ya hicimos y nos ayudó..."
    assert extract_done_signal("eso ya hicimos y nos ayudó a poner en evidencia") is True


def test_done_signal_detects_no_tiene_que_ver():
    assert extract_done_signal("eso ya no tiene que ver con lo que venimos hablando") is True


def test_done_signal_detects_el_punto_es():
    assert extract_done_signal("creo que el punto es entender por qué no se adaptan") is True


def test_done_signal_detects_english():
    assert extract_done_signal("we already covered that") is True
    assert extract_done_signal("that's off topic") is True
    assert extract_done_signal("I already answered that") is True


def test_done_signal_false_on_ordinary_reasoning():
    # The cow-example turn is substantive reasoning, not a done cue.
    cow = (
        "una vaca tiene un sistema digestivo muy particular y su dentadura "
        "es plana, pensada para masticar pastura, todo lo contrario a un carnívoro"
    )
    assert extract_done_signal(cow) is False


def test_done_signal_false_on_empty():
    assert extract_done_signal("") is False
    assert extract_done_signal("   ") is False


# --- procedural_request ---

def test_procedural_request_zero_on_neutral():
    assert extract_procedural_request("la respuesta es 42") is False


def test_procedural_request_detects_spanish_no_recuerdo():
    assert extract_procedural_request("no recuerdo cómo se calcula") is True


def test_procedural_request_detects_spanish_dame_la_formula():
    assert extract_procedural_request("dame la formula") is True


def test_procedural_request_detects_english_dont_remember():
    assert extract_procedural_request("I don't remember the formula") is True


def test_procedural_request_detects_english_what_is_the_formula():
    assert extract_procedural_request("Could you give me the formula?") is True


def test_procedural_request_detects_english_dont_know_how_to():
    assert extract_procedural_request("I don't know how to calculate the area") is True


def test_procedural_request_does_not_match_confusion_phrasing():
    # "I don't understand" is concept-level confusion, not a procedural-fact request.
    assert extract_procedural_request("I don't understand the problem") is False


def test_procedural_request_does_not_match_generic_hedging():
    assert extract_procedural_request("I think the answer is large") is False


def test_procedural_request_zero_on_empty():
    assert extract_procedural_request("") is False
    assert extract_procedural_request("   ") is False


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
