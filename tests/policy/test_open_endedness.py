"""Tests for src/policy/interceptors/open_endedness_classifier.py."""
from src.policy.interceptors.open_endedness_classifier import (
    open_endedness_score,
    split_sentences,
)


# --- open_endedness_score ---

def test_zero_when_no_question_mark():
    assert open_endedness_score("La respuesta es 42.") == 0.0
    assert open_endedness_score("") == 0.0


def test_high_on_spanish_wh_stem():
    assert open_endedness_score("¿Qué te llevó a esa conclusión?") >= 0.6
    assert open_endedness_score("¿Cómo decidiste empezar por ahí?") >= 0.6
    assert open_endedness_score("¿Por qué creés que pasa eso?") >= 0.6
    assert open_endedness_score("¿Cuál es tu primera idea?") >= 0.6


def test_high_on_english_wh_stem():
    assert open_endedness_score("What is your first move?") >= 0.6
    assert open_endedness_score("Why does that step make sense?") >= 0.6
    assert open_endedness_score("How would you check that?") >= 0.6


def test_low_on_closed_yes_no():
    assert open_endedness_score("¿Entendiste?") <= 0.2
    assert open_endedness_score("¿Está claro?") <= 0.2
    assert open_endedness_score("¿Tiene sentido?") <= 0.2
    assert open_endedness_score("¿Ok?") <= 0.2
    assert open_endedness_score("¿No?") <= 0.2


def test_low_on_english_closed():
    assert open_endedness_score("Got it?") <= 0.2
    assert open_endedness_score("Does that make sense?") <= 0.2
    assert open_endedness_score("Right?") <= 0.2


def test_mid_score_on_yes_no_without_closed_pattern():
    # Interrogative form without wh-stem and not a known rhetorical pattern.
    score = open_endedness_score("¿Probaste con ese enfoque?")
    assert 0.2 < score < 0.6


# --- split_sentences ---

def test_split_simple():
    out = split_sentences("Hola. ¿Cómo estás? Genial.")
    assert len(out) == 3
    assert out[0] == "Hola."
    assert out[1] == "¿Cómo estás?"


def test_split_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_split_keeps_terminators():
    out = split_sentences("Sí. ¿Por qué?")
    assert "?" in out[-1]
    assert "." in out[0]


def test_split_single_sentence():
    out = split_sentences("¿Qué pensás?")
    assert out == ["¿Qué pensás?"]
