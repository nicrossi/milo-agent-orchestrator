"""Adversarial tests for RhetoricalQuestionDetectorInterceptor."""
from src.policy.interceptors.rhetorical_question_detector import (
    RhetoricalQuestionDetectorInterceptor,
)


_FALLBACK_Q = "¿Qué crees que pasaría si...?"


def fire(text: str) -> tuple[bool, str]:
    return RhetoricalQuestionDetectorInterceptor().process(text, _FALLBACK_Q)


# --- should fire (assertion + rhetorical question) ---

def test_assertion_then_entendiste_fires():
    text = (
        "Los herbívoros desaparecerían sin alimento porque su población crecería "
        "demasiado. ¿Entendiste?"
    )
    modified, out = fire(text)
    assert modified
    assert _FALLBACK_Q in out


def test_assertion_then_esta_claro_fires():
    text = (
        "La cadena alimentaria se rompería en cascada y todos los consumidores "
        "se verían afectados. ¿Está claro?"
    )
    modified, _ = fire(text)
    assert modified


def test_assertion_then_no_fires():
    text = "Los molares son para vegetales y los caninos son para carne. ¿No?"
    modified, _ = fire(text)
    assert modified


def test_assertion_then_does_that_make_sense_fires():
    text = (
        "The herbivore population would explode because their predators are "
        "gone. Does that make sense?"
    )
    modified, _ = fire(text)
    assert modified


def test_appended_question_attached_at_end():
    text = "Una explicación larga sobre el ecosistema y sus interacciones. ¿Verdad?"
    modified, out = fire(text)
    assert modified
    assert out.endswith(_FALLBACK_Q)


# --- should NOT fire (genuine open-ended question present) ---

def test_genuine_wh_question_passes():
    text = "¿Qué crees que pasaría con los herbívoros?"
    modified, out = fire(text)
    assert not modified
    assert out == text


def test_assertion_then_open_question_passes():
    text = (
        "Hay varias formas de pensarlo. ¿Por qué creés que los herbívoros "
        "podrían tener problemas a largo plazo?"
    )
    modified, _ = fire(text)
    assert not modified


def test_two_open_questions_passes():
    text = "¿Qué pensás? ¿Cómo lo enfocarías?"
    modified, _ = fire(text)
    assert not modified


def test_no_question_mark_passes():
    text = "Los herbívoros aumentarían en cantidad."
    modified, _ = fire(text)
    assert not modified


def test_short_assertive_prefix_passes():
    # Short prefix = not "mostly assertion" — let DirectAnswerDetector handle.
    text = "Sí. ¿No?"
    modified, _ = fire(text)
    assert not modified


def test_empty_output_passes():
    modified, out = fire("")
    assert not modified
    assert out == ""


def test_english_open_question_passes():
    text = "There are many factors. What do you think would happen first?"
    modified, _ = fire(text)
    assert not modified


# --- edge cases ---

def test_long_assertion_then_short_rhetorical_fires():
    text = (
        "When a top predator is removed, the herbivore population can grow "
        "unchecked, which leads to overgrazing and ecosystem collapse over time. "
        "Right?"
    )
    modified, _ = fire(text)
    assert modified


def test_two_rhetorical_questions_fire():
    text = (
        "Los carnívoros desaparecen y los herbívoros se multiplican sin control. "
        "¿Entendiste? ¿Tiene sentido?"
    )
    modified, _ = fire(text)
    assert modified
