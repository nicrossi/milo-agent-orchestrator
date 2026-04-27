"""
Question families — pedagogical taxonomy used to tag every question in the bank.

Family names follow the SRL / tutoring literature (Graesser & Person Q-taxonomy,
Chi self-explanation, Hattie & Timperley feedback levels). See
deep-research-report.md §"Question families mapped to PLANNING, MONITORING,
and EVALUATION".

The selector chooses a family based on FSM state + Scores, then picks a
specific question within that family (round-robin, deduplicated by recent IDs).
"""
import enum


class QuestionFamily(str, enum.Enum):
    # PLANNING-leaning
    GOAL_CLARIFICATION = "GOAL_CLARIFICATION"          # "¿Qué querés lograr?"
    STRATEGY_REVISION = "STRATEGY_REVISION"            # "¿Qué estrategia probarías?"
    ATTEMPT_ELICITATION = "ATTEMPT_ELICITATION"        # "¿Qué probaste hasta ahora?"

    # MONITORING-leaning
    SELF_EXPLANATION = "SELF_EXPLANATION"              # "¿Por qué creés que eso funciona?"
    CALIBRATION = "CALIBRATION"                        # "¿Qué tan seguro estás?"
    DISCREPANCY_DETECTION = "DISCREPANCY_DETECTION"    # "¿Dónde podría estar el problema?"
    MONITORING_CHECK = "MONITORING_CHECK"              # "¿Cómo va tu plan?"

    # EVALUATION-leaning
    REATTRIBUTION = "REATTRIBUTION"                    # "¿Qué te ayudó a llegar?"
    TRANSFER = "TRANSFER"                              # "¿Cómo aplicarías esto en otro caso?"

    # Phase 4 (declared now so enum is stable)
    RECOVERY_STABILIZE = "RECOVERY_STABILIZE"          # narrowed-choice / validation
