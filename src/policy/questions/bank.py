"""
Question bank — tagged catalogue of metacognitive questions.

Each Question has:
  - id: stable identifier (used in recent_ids dedup, telemetry, evidence registry).
  - family: pedagogical family (see families.py).
  - state: FSM state where this question is appropriate.
  - surface_variants: 1+ wordings of the same pedagogical move. The selector
    rotates through variants to avoid repetition fatigue.
  - tags:
      difficulty: 1 (easy/scaffolded) | 2 (medium) | 3 (challenging)
      tone: "supportive" | "neutral" | "challenging"
      escalation_level: 0 (open) → 3 (most concrete) — used by Phase 4 hint ladder.
      requires_attempt: bool — if True, only fire when attempt_present.

Some surface variants contain {topic} placeholders. The contextualizer fills
these from the activity title at runtime; if no activity is bound, the
contextualizer leaves the placeholder as-is and the selector falls back to a
variant without placeholders.
"""
from typing import Literal

from pydantic import BaseModel, Field

from src.policy.questions.families import QuestionFamily
from src.policy.types import FSMState

ToneLiteral = Literal["supportive", "neutral", "challenging"]


class Question(BaseModel):
    id: str
    family: QuestionFamily
    state: FSMState
    surface_variants: list[str]
    difficulty: int = Field(default=2, ge=1, le=3)
    tone: ToneLiteral = "neutral"
    escalation_level: int = Field(default=0, ge=0, le=3)
    requires_attempt: bool = False


# ---------------------------------------------------------------------------
# Bank — 35 questions across all families.
# IDs use family prefix so they don't collide with FSM-state prefixes.
# ---------------------------------------------------------------------------

_BANK: list[Question] = [
    # --- PLANNING / GOAL_CLARIFICATION ---
    Question(
        id="goal_01",
        family=QuestionFamily.GOAL_CLARIFICATION,
        state=FSMState.PLANNING,
        surface_variants=[
            "¿Qué querés entender sobre {topic}?",
            "¿Qué te gustaría lograr en esta reflexión sobre {topic}?",
            "¿Qué es lo que querés lograr en esta sesión?",
        ],
        difficulty=1,
    ),
    Question(
        id="goal_02",
        family=QuestionFamily.GOAL_CLARIFICATION,
        state=FSMState.PLANNING,
        surface_variants=[
            "¿Cómo se ve el éxito para vos en este tema?",
            "¿Qué sería para vos haber entendido bien esto?",
        ],
        difficulty=2,
    ),
    Question(
        id="goal_03",
        family=QuestionFamily.GOAL_CLARIFICATION,
        state=FSMState.PLANNING,
        surface_variants=[
            "Antes de avanzar con {topic}, ¿qué necesitás clarificar?",
            "¿Qué parte del problema te resulta más difícil de definir?",
        ],
        difficulty=2,
    ),

    # --- PLANNING / STRATEGY_REVISION ---
    Question(
        id="strat_01",
        family=QuestionFamily.STRATEGY_REVISION,
        state=FSMState.PLANNING,
        surface_variants=[
            "¿Qué estrategia pensás usar para empezar?",
            "¿Cuál sería tu primer movimiento?",
        ],
        difficulty=2,
    ),
    Question(
        id="strat_02",
        family=QuestionFamily.STRATEGY_REVISION,
        state=FSMState.PLANNING,
        surface_variants=[
            "¿Qué regla o idea creés que podría ayudarte primero?",
            "¿Hay algún enfoque parecido que ya probaste antes?",
        ],
        difficulty=2,
    ),

    # --- PLANNING / ATTEMPT_ELICITATION (used by ElicitAttemptRule) ---
    Question(
        id="elicit_01",
        family=QuestionFamily.ATTEMPT_ELICITATION,
        state=FSMState.PLANNING,
        surface_variants=[
            "Antes de avanzar, ¿qué probaste hasta ahora?",
            "¿Qué pasos diste hasta este momento?",
        ],
        tone="supportive",
        difficulty=1,
    ),
    Question(
        id="elicit_02",
        family=QuestionFamily.ATTEMPT_ELICITATION,
        state=FSMState.PLANNING,
        surface_variants=[
            "Para ayudarte mejor, ¿podrías contarme cómo lo enfocaste?",
            "¿Qué camino estuviste explorando?",
        ],
        tone="supportive",
        difficulty=1,
    ),
    Question(
        id="elicit_03",
        family=QuestionFamily.ATTEMPT_ELICITATION,
        state=FSMState.PLANNING,
        surface_variants=[
            "¿Cuál es tu primera intuición sobre esto, aunque no estés seguro?",
            "¿Qué idea se te viene a la mente, incluso si no estás del todo seguro?",
        ],
        tone="supportive",
        difficulty=1,
    ),
    Question(
        id="elicit_04",
        family=QuestionFamily.ATTEMPT_ELICITATION,
        state=FSMState.PLANNING,
        surface_variants=[
            "¿Qué parte lograste resolver y dónde te trabaste?",
            "¿Hay algo que pudiste avanzar y algo que te frenó?",
        ],
        tone="supportive",
        difficulty=2,
    ),
    Question(
        id="elicit_05",
        family=QuestionFamily.ATTEMPT_ELICITATION,
        state=FSMState.PLANNING,
        surface_variants=[
            "Si tuvieras que arrancar por algún lado, ¿cuál sería?",
            "¿Por dónde empezarías si tuvieras que dar un primer paso?",
        ],
        tone="supportive",
        difficulty=1,
    ),

    # --- MONITORING / SELF_EXPLANATION ---
    Question(
        id="explain_01",
        family=QuestionFamily.SELF_EXPLANATION,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Por qué creés que ese paso tiene sentido?",
            "¿Qué principio estás usando ahí?",
        ],
        difficulty=2,
        requires_attempt=True,
    ),
    Question(
        id="explain_02",
        family=QuestionFamily.SELF_EXPLANATION,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Cómo decidiste ir por ese camino?",
            "¿Qué te llevó a elegir ese enfoque?",
        ],
        difficulty=2,
        requires_attempt=True,
    ),
    Question(
        id="explain_03",
        family=QuestionFamily.SELF_EXPLANATION,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Podrías contarme con tus palabras qué está pasando ahí?",
            "Si tuvieras que explicárselo a alguien que recién empieza, ¿cómo lo dirías?",
        ],
        difficulty=3,
    ),

    # --- MONITORING / CALIBRATION ---
    Question(
        id="calib_01",
        family=QuestionFamily.CALIBRATION,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Qué tan seguro estás de esto, y qué te hace pensar así?",
            "Si tuvieras que apostar, ¿qué tan firme te sentís con esa idea?",
        ],
        tone="challenging",
        difficulty=2,
    ),
    Question(
        id="calib_02",
        family=QuestionFamily.CALIBRATION,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Qué parte de tu razonamiento sentís sólida, y cuál más floja?",
            "¿Dónde te sentís firme, y dónde tenés más dudas?",
        ],
        tone="neutral",
        difficulty=2,
    ),

    # --- MONITORING / DISCREPANCY_DETECTION ---
    Question(
        id="discrep_01",
        family=QuestionFamily.DISCREPANCY_DETECTION,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Dónde podría estar el desajuste en esa idea?",
            "¿Qué supuesto podría estar fallando?",
        ],
        tone="challenging",
        difficulty=3,
        requires_attempt=True,
    ),
    Question(
        id="discrep_02",
        family=QuestionFamily.DISCREPANCY_DETECTION,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Hay algún caso donde tu razonamiento no funcione?",
            "¿Se te ocurre un contraejemplo que rompa esa idea?",
        ],
        tone="challenging",
        difficulty=3,
    ),

    # --- MONITORING / MONITORING_CHECK ---
    Question(
        id="check_01",
        family=QuestionFamily.MONITORING_CHECK,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Cómo vas con lo que planeabas hacer?",
            "¿Estás avanzando como esperabas, o algo cambió?",
        ],
        difficulty=1,
    ),
    Question(
        id="check_02",
        family=QuestionFamily.MONITORING_CHECK,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Qué obstáculos encontraste hasta ahora?",
            "¿Hay algo que te esté frenando en este momento?",
        ],
        difficulty=2,
    ),
    Question(
        id="check_03",
        family=QuestionFamily.MONITORING_CHECK,
        state=FSMState.MONITORING,
        surface_variants=[
            "¿Necesitás ajustar tu enfoque? ¿Por qué?",
            "¿Tu plan inicial sigue funcionando, o conviene revisarlo?",
        ],
        difficulty=2,
    ),

    # --- MONITORING / STRATEGY_REVISION (mid-task pivot) ---
    Question(
        id="strat_03",
        family=QuestionFamily.STRATEGY_REVISION,
        state=FSMState.MONITORING,
        surface_variants=[
            "Si lo que estás haciendo no avanza, ¿qué otra estrategia probarías?",
            "¿Qué cambiarías de tu enfoque actual?",
        ],
        difficulty=2,
        requires_attempt=True,
    ),

    # --- EVALUATION / REATTRIBUTION ---
    Question(
        id="attrib_01",
        family=QuestionFamily.REATTRIBUTION,
        state=FSMState.EVALUATION,
        surface_variants=[
            "¿Qué movida fue la que realmente te ayudó a destrabar esto?",
            "¿Cuál fue el momento clave de tu razonamiento?",
        ],
        difficulty=2,
    ),
    Question(
        id="attrib_02",
        family=QuestionFamily.REATTRIBUTION,
        state=FSMState.EVALUATION,
        surface_variants=[
            "¿Lo que te trabó fue de estrategia, atención, conocimiento previo, u otra cosa?",
            "Si tuvieras que nombrar la causa de tu dificultad, ¿qué fue?",
        ],
        difficulty=3,
    ),

    # --- EVALUATION / TRANSFER ---
    Question(
        id="transfer_01",
        family=QuestionFamily.TRANSFER,
        state=FSMState.EVALUATION,
        surface_variants=[
            "¿Cómo cambiaría esto en un caso parecido pero distinto?",
            "Si te aparece un problema parecido en otra situación, ¿qué llevarías de acá?",
        ],
        difficulty=3,
    ),
    Question(
        id="transfer_02",
        family=QuestionFamily.TRANSFER,
        state=FSMState.EVALUATION,
        surface_variants=[
            "¿Qué regla general te llevás de esta reflexión?",
            "¿Hay algo de lo que pensaste hoy que sirva fuera de {topic}?",
        ],
        difficulty=3,
    ),

    # --- EVALUATION / SELF_EXPLANATION (looking back) ---
    Question(
        id="reflect_01",
        family=QuestionFamily.SELF_EXPLANATION,
        state=FSMState.EVALUATION,
        surface_variants=[
            "¿Qué aprendiste en esta sesión?",
            "¿Qué te llevás claro de este intercambio?",
        ],
        difficulty=1,
    ),
    Question(
        id="reflect_02",
        family=QuestionFamily.SELF_EXPLANATION,
        state=FSMState.EVALUATION,
        surface_variants=[
            "¿Cómo se conecta lo de hoy con lo que ya sabías?",
            "¿Esto te cambió o reafirmó alguna idea previa?",
        ],
        difficulty=2,
    ),
    Question(
        id="reflect_03",
        family=QuestionFamily.SELF_EXPLANATION,
        state=FSMState.EVALUATION,
        surface_variants=[
            "¿Harías algo distinto la próxima vez?",
            "¿Qué te llevarías para una próxima reflexión parecida?",
        ],
        difficulty=2,
    ),

    # --- EVALUATION / CALIBRATION (final check) ---
    Question(
        id="calib_eval_01",
        family=QuestionFamily.CALIBRATION,
        state=FSMState.EVALUATION,
        surface_variants=[
            "¿Qué parte de {topic} todavía te genera dudas?",
            "¿Qué siente que entendiste y qué siente que sigue oscuro?",
        ],
        difficulty=2,
    ),

    # --- RECOVERY_STABILIZE (Phase 4 will use these; pre-populated for stability) ---
    Question(
        id="recover_01",
        family=QuestionFamily.RECOVERY_STABILIZE,
        state=FSMState.PLANNING,
        surface_variants=[
            "Tomemos esto más despacio. ¿Te sirve si lo dividimos en dos partes?",
            "Vamos paso a paso. ¿Qué parte querés revisar primero, A o B?",
        ],
        tone="supportive",
        difficulty=1,
    ),
    Question(
        id="recover_02",
        family=QuestionFamily.RECOVERY_STABILIZE,
        state=FSMState.MONITORING,
        surface_variants=[
            "Es normal sentirse perdido a veces. ¿Volvemos al plan original?",
            "Hagamos una pausa. ¿Qué fue lo último que sí entendiste bien?",
        ],
        tone="supportive",
        difficulty=1,
    ),
]


def all_questions() -> list[Question]:
    """Return the full bank (read-only)."""
    return list(_BANK)


def by_state(state: FSMState) -> list[Question]:
    return [q for q in _BANK if q.state == state]


def by_family(family: QuestionFamily) -> list[Question]:
    return [q for q in _BANK if q.family == family]


def by_state_and_family(state: FSMState, family: QuestionFamily) -> list[Question]:
    return [q for q in _BANK if q.state == state and q.family == family]


def by_id(qid: str) -> Question | None:
    for q in _BANK:
        if q.id == qid:
            return q
    return None
