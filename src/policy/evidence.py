"""
Evidence registry — paper-to-rule mapping for thesis-grade traceability.

Every deterministic component (rule, interceptor, score formula, FSM state)
in the policy engine should be defensible against a research source. This
module declares the canonical citations and binds them to component names.

Each rule/interceptor file declares an `__evidence__: list[str]` constant
listing the keys here that justify it. `GET /policy/evidence` returns a
flattened view.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Citation:
    key: str
    author: str
    year: int
    claim: str
    source_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# Canonical citations referenced across the engine. Keys are stable IDs used
# in __evidence__ constants and in the GET /policy/evidence response.
_CITATIONS: list[Citation] = [
    Citation(
        key="zimmerman_2002_srl_cycle",
        author="Zimmerman",
        year=2002,
        claim=(
            "Self-regulated learning is a cycle of forethought, performance, "
            "and self-reflection — supporting a 3-state PLANNING/MONITORING/"
            "EVALUATION FSM."
        ),
        source_url="https://doi.org/10.1207/s15430421tip4102_2",
    ),
    Citation(
        key="winne_hadwin_1998_traces",
        author="Winne & Hadwin",
        year=1998,
        claim=(
            "Studying produces traces (notes, hesitations, retries) that should "
            "feed metacognitive monitoring — justifies signal extraction beyond "
            "self-report confidence."
        ),
        source_url="",
    ),
    Citation(
        key="koedinger_aleven_2007_assistance_dilemma",
        author="Koedinger & Aleven",
        year=2007,
        claim=(
            "The assistance dilemma: too little help → unproductive struggle; "
            "too much → undermines learning. Justifies graduated hint ladder "
            "with bottom-out as last resort."
        ),
        source_url="https://doi.org/10.1007/s10648-007-9049-0",
    ),
    Citation(
        key="narciss_2008_informative_tutoring_feedback",
        author="Narciss",
        year=2008,
        claim=(
            "Informative tutoring feedback: elaborated guidance that helps the "
            "learner without immediately giving the correct response. "
            "Justifies the no-direct-answer rule and the focused-hint rung."
        ),
        source_url="",
    ),
    Citation(
        key="dmello_graesser_2012_confusion",
        author="D'Mello & Graesser",
        year=2012,
        claim=(
            "Confusion is the affective signature of cognitive disequilibrium; "
            "moderate confusion is productive, sustained confusion needs "
            "stabilization. Justifies RECOVERY_STABILIZE micro-state."
        ),
        source_url="https://doi.org/10.1016/j.learninstruc.2012.05.003",
    ),
    Citation(
        key="aleven_2003_help_seeking",
        author="Aleven et al.",
        year=2003,
        claim=(
            "≥72% of help-seeking actions in a tutor were unproductive (hint "
            "abuse, help avoidance). Intervening on >75% of actions becomes "
            "annoying — justifies meta-feedback cooldown."
        ),
        source_url="",
    ),
    Citation(
        key="chi_1994_self_explanation",
        author="Chi et al.",
        year=1994,
        claim=(
            "Prompted self-explanation improves understanding — justifies "
            "SELF_EXPLANATION question family being a default in MONITORING."
        ),
        source_url="https://doi.org/10.1207/s1532690xci1304_3",
    ),
    Citation(
        key="graesser_person_1994_question_quality",
        author="Graesser & Person",
        year=1994,
        claim=(
            "Achievement correlates with the quality of questions, not their "
            "frequency. Justifies a tagged question bank with deliberate "
            "pedagogical families."
        ),
        source_url="",
    ),
    Citation(
        key="hattie_timperley_2007_feedback",
        author="Hattie & Timperley",
        year=2007,
        claim=(
            "Effective feedback targets task, process, or self-regulation — "
            "self-level praise is weakest. Justifies HintLadderRule directives "
            "framing process feedback first, not validation."
        ),
        source_url="https://doi.org/10.3102/003465430298487",
    ),
    Citation(
        key="aleven_koedinger_1999_explanation_transfer",
        author="Aleven & Koedinger",
        year=1999,
        claim=(
            "Requiring learners to justify their solution steps improves "
            "transfer. Justifies ElicitAttemptRule overriding the FSM-default "
            "question with an attempt-elicitation prompt."
        ),
        source_url="",
    ),
]

EVIDENCE_REGISTRY: dict[str, Citation] = {c.key: c for c in _CITATIONS}


# Component → list of citation keys. Mirrors the __evidence__ constants in
# rule/interceptor files. The endpoint cross-references both views.
COMPONENT_EVIDENCE: dict[str, list[str]] = {
    "fsm": ["zimmerman_2002_srl_cycle", "winne_hadwin_1998_traces"],
    "hint_ladder": [
        "koedinger_aleven_2007_assistance_dilemma",
        "narciss_2008_informative_tutoring_feedback",
        "hattie_timperley_2007_feedback",
    ],
    "recovery": ["dmello_graesser_2012_confusion"],
    "cooldown": ["aleven_2003_help_seeking"],
    "no_direct_answers_rule": ["narciss_2008_informative_tutoring_feedback"],
    "elicit_attempt_rule": ["aleven_koedinger_1999_explanation_transfer"],
    "tone_by_confidence_rule": ["hattie_timperley_2007_feedback"],
    "direct_answer_detector": ["narciss_2008_informative_tutoring_feedback"],
    "rhetorical_question_detector": ["graesser_person_1994_question_quality"],
    "question_bank": [
        "graesser_person_1994_question_quality",
        "chi_1994_self_explanation",
    ],
    "scores": ["winne_hadwin_1998_traces", "dmello_graesser_2012_confusion"],
}


def all_citations() -> list[Citation]:
    return list(EVIDENCE_REGISTRY.values())


def citations_for(component: str) -> list[Citation]:
    keys = COMPONENT_EVIDENCE.get(component, [])
    return [EVIDENCE_REGISTRY[k] for k in keys if k in EVIDENCE_REGISTRY]


def to_dict() -> dict:
    """Serializable view used by the GET /policy/evidence endpoint."""
    return {
        "citations": [c.to_dict() for c in all_citations()],
        "components": {
            comp: [EVIDENCE_REGISTRY[k].to_dict() for k in keys if k in EVIDENCE_REGISTRY]
            for comp, keys in COMPONENT_EVIDENCE.items()
        },
    }
