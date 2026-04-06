import enum
from typing import Literal

from pydantic import BaseModel, Field


class FSMState(str, enum.Enum):
    PLANNING = "PLANNING"
    MONITORING = "MONITORING"
    EVALUATION = "EVALUATION"


class UserSignals(BaseModel):
    # DEFERRED (R2): confidence is always 3 (neutral) in v1.
    # TODO: inject real confidence from client signal extraction.
    confidence: int = Field(default=3, ge=1, le=5)


class PolicyContext(BaseModel):
    current_state: FSMState
    turn_count: int                   # completed turns in this session (len(history) // 2)
    recent_question_ids: list[str]    # IDs of questions asked this session, most recent last
    user_message: str
    user_signals: UserSignals = Field(default_factory=UserSignals)


class ResponseConstraints(BaseModel):
    forbid_direct_answer: bool = True
    must_ask_question: bool = True


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
