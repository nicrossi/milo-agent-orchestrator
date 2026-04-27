"""
Question selector — picks a Question for a given turn from the tagged bank.

Selection algorithm:
  1. Compute an ordered list of family preferences from FSM state + Scores.
     E.g. high miscalibration → prefer CALIBRATION or DISCREPANCY_DETECTION;
     high affect_load → prefer simpler / supportive families.
  2. For each family in order, pick the first question in (state, family) not
     in recent_question_ids and whose `requires_attempt` constraint is met.
  3. If no family yields a candidate, fall back to ANY question in the state
     not in recent_ids.
  4. If even that fails (all asked), cycle back to the first state-matching
     question (round-robin invariant).

Returns (Question, surface_variant_text).
"""
from typing import Optional

from src.policy.questions.bank import Question, by_family, by_state, by_state_and_family
from src.policy.questions.families import QuestionFamily
from src.policy.types import ActivityRef, FSMState, Scores, UserSignals

# Score thresholds for family-preference routing.
_AFFECT_LOAD_HIGH = 0.6
_MISCALIBRATION_HIGH = 0.5
_STRUGGLE_HIGH = 0.6


def family_preference(
    state: FSMState,
    scores: Optional[Scores],
    user_signals: Optional[UserSignals] = None,
) -> list[QuestionFamily]:
    """Ordered family preferences for this turn (most preferred first).

    Falls back to a state-default ordering when scores are absent or neutral.
    """
    if scores is not None:
        # High-priority overrides
        if scores.affect_load > _AFFECT_LOAD_HIGH:
            # The student is overloaded — prefer simpler / validating families.
            if state == FSMState.PLANNING:
                return [
                    QuestionFamily.GOAL_CLARIFICATION,
                    QuestionFamily.ATTEMPT_ELICITATION,
                    QuestionFamily.STRATEGY_REVISION,
                ]
            if state == FSMState.MONITORING:
                return [
                    QuestionFamily.MONITORING_CHECK,
                    QuestionFamily.SELF_EXPLANATION,
                ]
            if state == FSMState.EVALUATION:
                return [
                    QuestionFamily.SELF_EXPLANATION,
                    QuestionFamily.REATTRIBUTION,
                ]

        if scores.miscalibration > _MISCALIBRATION_HIGH:
            # Confident but signals suggest off-track — push for justification.
            if state == FSMState.MONITORING:
                return [
                    QuestionFamily.CALIBRATION,
                    QuestionFamily.DISCREPANCY_DETECTION,
                    QuestionFamily.SELF_EXPLANATION,
                ]
            if state == FSMState.EVALUATION:
                return [
                    QuestionFamily.CALIBRATION,
                    QuestionFamily.REATTRIBUTION,
                ]
            # PLANNING: still ask for grounding
            return [
                QuestionFamily.STRATEGY_REVISION,
                QuestionFamily.GOAL_CLARIFICATION,
            ]

        if scores.struggle > _STRUGGLE_HIGH:
            # Productive struggle — articulate it.
            if state == FSMState.MONITORING:
                return [
                    QuestionFamily.SELF_EXPLANATION,
                    QuestionFamily.STRATEGY_REVISION,
                    QuestionFamily.DISCREPANCY_DETECTION,
                ]
            if state == FSMState.PLANNING:
                return [
                    QuestionFamily.STRATEGY_REVISION,
                    QuestionFamily.GOAL_CLARIFICATION,
                ]
            if state == FSMState.EVALUATION:
                return [
                    QuestionFamily.REATTRIBUTION,
                    QuestionFamily.SELF_EXPLANATION,
                ]

    # State defaults (no score override)
    if state == FSMState.PLANNING:
        return [
            QuestionFamily.GOAL_CLARIFICATION,
            QuestionFamily.STRATEGY_REVISION,
        ]
    if state == FSMState.MONITORING:
        return [
            QuestionFamily.MONITORING_CHECK,
            QuestionFamily.SELF_EXPLANATION,
            QuestionFamily.CALIBRATION,
        ]
    # EVALUATION
    return [
        QuestionFamily.SELF_EXPLANATION,
        QuestionFamily.REATTRIBUTION,
        QuestionFamily.TRANSFER,
    ]


def _meets_attempt_constraint(q: Question, user_signals: Optional[UserSignals]) -> bool:
    if not q.requires_attempt:
        return True
    if user_signals is None:
        # Without signals, be permissive — Phase 1 ensured signals are populated
        # in the real flow; rule tests may construct contexts without them.
        return True
    return user_signals.attempt_present


def select_question(
    state: FSMState,
    scores: Optional[Scores],
    recent_ids: list[str],
    activity: Optional[ActivityRef] = None,
    user_signals: Optional[UserSignals] = None,
    force_family: Optional[QuestionFamily] = None,
) -> tuple[Question, str]:
    """Pick a Question + surface variant for this turn.

    Args:
        force_family: when set, bypasses the score-driven family preference
            and selects only from this family. Used by Phase 4 recovery to
            force RECOVERY_STABILIZE questions while in STABILIZE.

    Returns (question, variant_text). The variant is NOT yet contextualized —
    the engine calls contextualize() separately to substitute {topic}.
    """
    if force_family is not None:
        # Strict override: ignore state filter for the lookup so e.g. recovery
        # questions can fire in any FSM state.
        candidates = by_family(force_family)
        for q in candidates:
            if q.id in recent_ids:
                continue
            if not _meets_attempt_constraint(q, user_signals):
                continue
            return q, q.surface_variants[0]
        if candidates:
            return candidates[0], candidates[0].surface_variants[0]
        # Family is empty — fall through to normal preference logic.
        preferences = family_preference(state, scores, user_signals)
    else:
        preferences = family_preference(state, scores, user_signals)

    # Pass 1: try each preferred family in order, skipping recent IDs and
    # respecting requires_attempt.
    for family in preferences:
        for q in by_state_and_family(state, family):
            if q.id in recent_ids:
                continue
            if not _meets_attempt_constraint(q, user_signals):
                continue
            return q, q.surface_variants[0]

    # Pass 2: any question in this state, skipping recent.
    for q in by_state(state):
        if q.id in recent_ids:
            continue
        if not _meets_attempt_constraint(q, user_signals):
            continue
        return q, q.surface_variants[0]

    # Pass 3: round-robin fallback — first question in state regardless of
    # recent_ids (matches legacy behavior of question_bank.select_question).
    candidates = by_state(state)
    if not candidates:
        # Should never happen — bank covers all states. Defensive.
        from src.policy.questions.bank import all_questions
        q = all_questions()[0]
        return q, q.surface_variants[0]
    return candidates[0], candidates[0].surface_variants[0]
