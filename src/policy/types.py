import enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class FSMState(str, enum.Enum):
    PLANNING = "PLANNING"
    MONITORING = "MONITORING"
    EVALUATION = "EVALUATION"


class HintLadderState(str, enum.Enum):
    """Phase 4 — assistance-dilemma escalation rungs.

    Order: PROCESS_FEEDBACK → STRATEGIC_HINT → FOCUSED_HINT → BOTTOM_OUT.
    Bottom-out is a near-answer fallback and should be reached only after
    sustained unproductive struggle.
    """
    PROCESS_FEEDBACK = "PROCESS_FEEDBACK"
    STRATEGIC_HINT = "STRATEGIC_HINT"
    FOCUSED_HINT = "FOCUSED_HINT"
    BOTTOM_OUT = "BOTTOM_OUT"


class RecoveryState(str, enum.Enum):
    """Phase 4 — confusion-recovery micro-state.

    NORMAL: standard pedagogical flow.
    STABILIZE: learner shows confusion + affect overload; pause FSM
    transitions, force RECOVERY_STABILIZE family questions, block bottom-out.
    """
    NORMAL = "NORMAL"
    STABILIZE = "STABILIZE"


class UserSignals(BaseModel):
    """
    Per-turn learner-state signals derived from text + server-side timing.

    All fields default to safe-neutral values so callers can construct
    UserSignals() without args.

    Phase 6 removed the legacy `confidence` field — `affect_load`,
    `miscalibration`, and `struggle` (computed in `Scores`) replace its
    pedagogical role with multi-dimensional signals derived from real text.
    """

    hedging: float = Field(default=0.0, ge=0.0, le=1.0)
    confusion: float = Field(default=0.0, ge=0.0, le=1.0)
    attempt_present: bool = True
    direct_answer_request: bool = False
    latency_z: float = 0.0
    length_z: float = 0.0
    revisions: int = Field(default=0, ge=0)


class Scores(BaseModel):
    """
    Derived per-turn scores aggregated from a window of UserSignals.

    All scores are in [0.0, 1.0]:
      - struggle: composite of hedging + confusion + slow latency + low effort
      - miscalibration: high confidence-proxy paired with lack-of-attempt cues
      - hint_abuse: repeated direct-answer requests with quick latency
      - help_avoidance: stalled (slow + no progress markers)
      - affect_load: confusion + low confidence proxy
    """

    struggle: float = Field(default=0.0, ge=0.0, le=1.0)
    miscalibration: float = Field(default=0.0, ge=0.0, le=1.0)
    hint_abuse: float = Field(default=0.0, ge=0.0, le=1.0)
    help_avoidance: float = Field(default=0.0, ge=0.0, le=1.0)
    affect_load: float = Field(default=0.0, ge=0.0, le=1.0)


class ActivityRef(BaseModel):
    """Lightweight reference to the active reflection activity.

    Caller (session.py) constructs this from ReflectionActivity once at session
    setup and passes it on every PolicyContext. The selector uses `title` for
    {topic} substitution; the contextualizer optionally uses
    `context_description` and `teacher_goal` for richer templating.
    """
    id: str
    title: str = ""
    teacher_goal: str = ""
    context_description: str = ""


class PolicyContext(BaseModel):
    current_state: FSMState
    turn_count: int                   # completed turns in this session (len(history) // 2)
    recent_question_ids: list[str]    # IDs of questions asked this session, most recent last
    user_message: str
    user_signals: UserSignals = Field(default_factory=UserSignals)
    # Rolling window of recent UserSignals (most recent last). Caller-owned.
    signals_window: list[UserSignals] = Field(default_factory=list)
    # Populated by PolicyEngine.evaluate() before rules run; do not pre-fill.
    scores: Optional[Scores] = None
    # Active reflection activity (Phase 3); None when no DB-backed activity.
    activity: Optional[ActivityRef] = None
    # Phase 4 — caller-owned cross-turn state. Defaults make a fresh session
    # start at the lowest hint rung, not in recovery, with cooldown disabled.
    hint_state: HintLadderState = HintLadderState.PROCESS_FEEDBACK
    turns_in_hint_state: int = 0
    consecutive_low_struggle_turns: int = 0
    recovery_state: RecoveryState = RecoveryState.NORMAL
    turns_in_recovery: int = 0
    # High default = first turn never suppressed by cooldown.
    turns_since_meta_feedback: int = 99


class ResponseConstraints(BaseModel):
    forbid_direct_answer: bool = True
    must_ask_question: bool = True
    # Phase 2: when True, the planned question is overridden with one from
    # the ATTEMPT_ELICITATION pool (set by ElicitAttemptRule).
    must_elicit_attempt: bool = False


class QuestionPlan(BaseModel):
    question_id: str
    question_text: str
    tone: Literal["supportive", "neutral", "challenging"] = "neutral"
    constraints: ResponseConstraints = Field(default_factory=ResponseConstraints)
    prompt_directives: list[str] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    next_state: FSMState
    plan: QuestionPlan
    applied_rules: list[str] = Field(default_factory=list)
    # Names of interceptors to run post-LLM
    interceptors: list[str] = Field(default_factory=list)
    # Scores computed during evaluate() — exposed for logging/observability.
    scores: Optional[Scores] = None
    # Phase 4 — next values of cross-turn state. Caller (session) persists these
    # back onto its in-memory state for the next turn.
    next_hint_state: HintLadderState = HintLadderState.PROCESS_FEEDBACK
    next_turns_in_hint_state: int = 0
    next_consecutive_low_struggle_turns: int = 0
    next_recovery_state: RecoveryState = RecoveryState.NORMAL
    next_turns_in_recovery: int = 0
    next_turns_since_meta_feedback: int = 99
