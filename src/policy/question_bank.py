"""
DEPRECATED (Phase 3): legacy compat shim.

The Phase 3 question system lives in `src.policy.questions.*` (bank, selector,
contextualizer, families). The engine imports from there directly.

This module preserves the legacy `QUESTION_BANK[FSMState] -> list[(id, text)]`
dict and `select_question(state, recent_ids)` signature so existing tests in
test_question_bank.py keep passing without rewrite. New code should import
from `src.policy.questions`. Do not extend this file.
"""
from src.policy.types import FSMState

QUESTION_BANK: dict[FSMState, list[tuple[str, str]]] = {
    FSMState.PLANNING: [
        ("plan_01", "¿Qué es lo que querés lograr en esta sesión?"),
        ("plan_02", "¿Cómo se ve el éxito para vos en este tema?"),
        ("plan_03", "¿Qué estrategia pensás usar para empezar?"),
        ("plan_04", "¿Qué parte de esto te resulta más difícil de definir?"),
        ("plan_05", "¿Qué necesitás clarificar antes de avanzar?"),
        ("plan_06", "¿Cómo medirías si estás progresando?"),
    ],
    FSMState.MONITORING: [
        ("mon_01", "¿Cómo vas con lo que planeabas hacer?"),
        ("mon_02", "¿Qué tan bien creés que estás entendiendo el material?"),
        ("mon_03", "¿Qué parte de tu plan estás siguiendo bien y cuál no tanto?"),
        ("mon_04", "¿Qué obstáculos encontraste hasta ahora?"),
        ("mon_05", "¿Necesitás ajustar tu enfoque? ¿Por qué?"),
        ("mon_06", "¿Cómo sabés que estás avanzando en la dirección correcta?"),
    ],
    FSMState.EVALUATION: [
        ("eval_01", "¿Qué aprendiste en esta sesión?"),
        ("eval_02", "¿Qué harías diferente la próxima vez?"),
        ("eval_03", "¿Cómo se conecta lo que aprendiste hoy con lo que ya sabías?"),
        ("eval_04", "¿Qué parte de este tema todavía te genera dudas?"),
        ("eval_05", "¿Cómo podrías aplicar lo que aprendiste en otro contexto?"),
    ],
}


def select_question(state: FSMState, recent_ids: list[str]) -> tuple[str, str]:
    """Return (question_id, question_text). Never raises.

    Skips questions whose IDs appear in recent_ids.
    Falls back to the first question in the list if all have been asked.
    """
    candidates = QUESTION_BANK[state]
    for qid, qtext in candidates:
        if qid not in recent_ids:
            return qid, qtext
    # All questions asked — cycle back to the first
    return candidates[0]
