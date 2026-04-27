"""Tests for src/policy/questions/contextualizer.py."""
from src.policy.questions.contextualizer import contextualize, has_topic_placeholder
from src.policy.types import ActivityRef


def _activity(title="El bosque sin depredadores") -> ActivityRef:
    return ActivityRef(
        id="abc-123",
        title=title,
        teacher_goal="Test goal",
        context_description="Test context.",
    )


def test_contextualize_substitutes_topic_placeholder():
    out = contextualize("¿Qué querés entender sobre {topic}?", _activity())
    assert out == "¿Qué querés entender sobre El bosque sin depredadores?"


def test_contextualize_passthrough_when_no_placeholder():
    text = "¿Cómo vas con tu plan?"
    assert contextualize(text, _activity()) == text


def test_contextualize_passthrough_when_no_activity():
    text = "¿Cómo vas con tu plan?"
    assert contextualize(text, None) == text


def test_contextualize_falls_back_when_no_activity_but_placeholder():
    out = contextualize("¿Qué querés sobre {topic}?", None)
    assert "{topic}" not in out
    assert "este tema" in out


def test_contextualize_falls_back_when_empty_title():
    activity = _activity(title="")
    out = contextualize("¿Qué probaste con {topic}?", activity)
    assert "{topic}" not in out
    assert "este tema" in out


def test_contextualize_substitutes_multiple_placeholders():
    activity = _activity(title="X")
    out = contextualize("Sobre {topic}, ¿qué pensás de {topic}?", activity)
    assert out.count("X") == 2
    assert "{topic}" not in out


def test_has_topic_placeholder_detects_placeholder():
    assert has_topic_placeholder("Sobre {topic}, ¿qué pensás?") is True
    assert has_topic_placeholder("¿Qué pensás?") is False
