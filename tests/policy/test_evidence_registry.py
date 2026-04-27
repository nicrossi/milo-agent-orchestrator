"""Tests for src/policy/evidence.py — registry coverage + serialization."""
from src.policy import evidence


def test_at_least_eight_citations():
    """Spec acceptance: ≥ 8 citations covering core components."""
    assert len(evidence.all_citations()) >= 8


def test_each_component_has_at_least_one_citation():
    for component, keys in evidence.COMPONENT_EVIDENCE.items():
        assert len(keys) >= 1, f"Component {component!r} has no citations"


def test_all_component_keys_resolve():
    """Every key referenced by COMPONENT_EVIDENCE must exist in EVIDENCE_REGISTRY."""
    for component, keys in evidence.COMPONENT_EVIDENCE.items():
        for k in keys:
            assert k in evidence.EVIDENCE_REGISTRY, (
                f"Component {component!r} references missing citation {k!r}"
            )


def test_critical_components_covered():
    required_components = {
        "fsm",
        "hint_ladder",
        "recovery",
        "cooldown",
        "no_direct_answers_rule",
        "elicit_attempt_rule",
        "direct_answer_detector",
        "rhetorical_question_detector",
    }
    assert required_components.issubset(evidence.COMPONENT_EVIDENCE.keys())


def test_to_dict_is_serializable():
    """The endpoint payload should be a plain JSON-friendly dict."""
    data = evidence.to_dict()
    assert "citations" in data
    assert "components" in data
    assert isinstance(data["citations"], list)
    assert isinstance(data["components"], dict)
    # Each citation has the expected fields.
    for c in data["citations"]:
        assert "key" in c and "author" in c and "year" in c and "claim" in c


def test_citations_for_known_component():
    cites = evidence.citations_for("hint_ladder")
    assert len(cites) >= 1
    assert any("assistance" in c.claim.lower() for c in cites)


def test_citations_for_unknown_component_returns_empty():
    assert evidence.citations_for("does_not_exist") == []


def test_rule_files_declare_evidence():
    """Each rule/interceptor module exposes __evidence__ keys that resolve."""
    from src.policy.rules import elicit_attempt, hint_ladder_rule, no_direct_answers, tone_by_confidence
    from src.policy.interceptors import direct_answer_detector, rhetorical_question_detector

    modules = [
        elicit_attempt,
        hint_ladder_rule,
        no_direct_answers,
        tone_by_confidence,
        direct_answer_detector,
        rhetorical_question_detector,
    ]
    for mod in modules:
        keys = getattr(mod, "__evidence__", None)
        assert keys, f"{mod.__name__} missing __evidence__ list"
        for k in keys:
            assert k in evidence.EVIDENCE_REGISTRY, (
                f"{mod.__name__} references missing citation {k!r}"
            )
