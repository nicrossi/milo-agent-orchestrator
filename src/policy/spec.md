# Policy Engine Implementation Spec

## 1. Overview

The Policy Engine is a deterministic, non-LLM module that controls Milo's pedagogical behavior on every turn. It decides **how** the system responds — not **what** it says.

It is responsible for:
- Selecting the current metacognitive state (PLANNING → MONITORING → EVALUATION) via a Finite State Machine
- Selecting a guiding question aligned with that state, avoiding repetition
- Applying guardrail rules (tone adaptation, no-direct-answer enforcement)
- Injecting prompt directives into the LLM context
- Post-processing LLM output to detect and correct violations

It fits the current system as a new `src/policy/` module integrated at two points in the existing pipeline:
1. **Pre-LLM** (inside `OrchestratorAgent.process_session_stream`): inject directives into context
2. **Post-stream** (inside `ChatSession._process_turn`): inspect accumulated text, append correction if needed

---

## 2. Repository Findings

### 2.1 Directory structure (current branch: `policy_engine_v2`)

```
src/
├── main.py                              # FastAPI app, lifespan, CORS
├── api/
│   ├── routers/chat.py                  # HTTP POST + WebSocket endpoints
│   └── session.py                       # ChatSession (WebSocket lifecycle owner)
├── orchestration/
│   └── agent.py                         # OrchestratorAgent (RAG + LLM pipeline)
├── adapters/
│   ├── clients/chat_history.py          # ChatHistoryRepository (DB access)
│   └── llm/
│       ├── base.py                      # BaseLLMAdapter interface
│       └── gemini.py                    # GeminiAdapter (Gemini API, streaming)
├── core/
│   ├── database.py                      # Async SQLAlchemy engine + session factory
│   ├── models.py                        # ORM: ChatMessage, ChatSession, SessionMetric, etc.
│   └── auth.py                          # Firebase token verification
├── schemas/
│   ├── chat.py                          # ChatRequest, ChatResponse
│   └── activities.py                    # Activity + metrics schemas
├── services/
│   ├── rag.py                           # Embedding + pgvector retrieval
│   └── metrics_evaluator.py             # Post-session LLM evaluation
├── prompts/milo_base_context.md         # Milo identity + behavior rules (loaded at runtime)
└── metrics/                             # Evaluation rubrics, schema, examples
```

**`src/policy/` does not exist on this branch.** It must be built from scratch.

`tests/policy/__pycache__/` contains compiled bytecode for `test_fsm.py`, `test_rules.py`, `test_interceptor.py`, `test_question_bank.py` — source files were written on a prior branch but are not present here. They serve as confirmation that tests were already designed and their structure is known.

### 2.2 End-to-end turn flow (WebSocket path — the stateful path)

```
WebSocket /chat/activities/{activity_id}
  └─ require_ws_user()                              [auth.py] — Firebase token → AuthenticatedUser
        └─ ChatSession.run()                        [session.py:62]
              ├─ _setup_db_session()               [session.py:86] — creates ChatSession ORM row, loads context_description
              ├─ _process_turn("")                  [session.py:168] — greeting (empty query)
              └─ _conversation_loop()              [session.py:126]
                    └─ _process_turn(user_text)    [session.py:168] — per user message
                          └─ agent.process_session_stream(db, user_id, session_id, user_text, context_description)
                                ├─ _load_history()              [agent.py:115] — loads from DB, limit=50
                                ├─ get_recent_cross_session_memory()  [chat_history.py:89] — up to 12 msgs across sessions
                                ├─ _persist_user_message()      [agent.py:127] — saves user msg to DB
                                ├─ rag_service.retrieve_context()  [rag.py] — embed + pgvector search
                                ├─ _compose_context()           [agent.py:59] — base + activity + memory + RAG
                                └─ _stream_and_persist()        [agent.py:133]
                                      ├─ llm_adapter.generate_answer_stream()  [gemini.py:101] — async token iterator
                                      ├─ yield chunk → ChatSession → ws.send_json({"type": "chunk", "text": ...})
                                      └─ _persist_model_response()  — joins chunks, saves to DB
                    └─ ws.send_json({"type": "done"})
              └─ _wrap_up_session()                [session.py:110] — sets PENDING_EVALUATION, fires background evaluator
```

**HTTP path** (`POST /chat`) is stateless and uses `process_query()` (no history, no streaming). The Policy Engine is **not needed there** in v1 — it is pedagogically irrelevant for one-shot queries.

### 2.3 Existing policy-like constructs

| Location | What it does | Status |
|---|---|---|
| `src/prompts/milo_base_context.md:8-11` | Soft behavioral rules ("prefer reflective questions", "do not give direct answers") | Exists — loaded as context chunk |
| `src/adapters/llm/gemini.py:14-31` — `SYSTEM_INSTRUCTION` | System-level instruction for Gemini; general tone and context-handling rules | Exists — hardcoded, the comment on line 13 says "Probably, we don't want this here!" |
| `src/core/models.py:63-67` — `SessionStatus` | Lifecycle FSM for sessions: IN_PROGRESS → PENDING_EVALUATION → EVALUATED | Exists — operational FSM, not pedagogical |
| `src/api/session.py:110-122` — `_wrap_up_session` | Triggers post-session evaluation | Exists |
| `src/services/metrics_evaluator.py` | Post-session LLM-based pedagogical grading (Reflection Quality, Calibration, Contextual Transfer) | Exists — but operates on completed sessions, not per-turn |

No per-turn pedagogical decision logic, FSM, question selection, or output interception exists anywhere in the codebase.

### 2.4 What the agent's process_session_stream currently accepts

```python
# agent.py:88
async def process_session_stream(
    self,
    db: AsyncSession,
    user_id: str,
    session_id: str,
    query: str,
    context_description: Optional[str] = None
) -> AsyncIterator[str]:
```

`_compose_context` at agent.py:59 assembles context as: `[base_context, activity_description, memory_block, *rag_chunks]`. There is no hook for injecting policy directives yet.

### 2.5 What ChatSession currently tracks

```python
# session.py:48-60
self._ws: WebSocket
self._user_id: str
self._agent: OrchestratorAgent
self._activity_id: str
self._background_tasks
self._session_id_uuid: UUID | None
self._session_id: str | None
self._context_description: str | None
self._created_tasks: List[asyncio.Task]
```

No FSM state, no turn counter, no question ID history — all missing.

### 2.6 WebSocket protocol (current)

Client → Server: plain `str` (raw user text)
Server → Client:
```json
{"type": "chunk", "text": "..."}   // per token
{"type": "done"}                    // turn complete
{"type": "error", "detail": "..."}  // error
```

No policy metadata is currently sent to the client.

---

## 3. Gap Analysis

### A. Already implemented

- Milo's identity and soft guardrail rules as context (`milo_base_context.md`)
- Session lifecycle FSM (operational, not pedagogical)
- Post-session metrics evaluation (`metrics_evaluator.py`)
- Streaming architecture with chunk accumulation in `_stream_and_persist`
- Chat history loading — `get_history()` returns `[{"role", "content"}]` ordered chronologically, limit 50

### B. Partially implemented

- **Prompt injection hook**: `_compose_context()` builds a list of context chunks but has no parameter for policy directives; needs one optional kwarg added
- **Turn count**: derivable from `len(history)` (number of persisted messages ÷ 2 = completed turns), but not explicitly tracked or stored
- **Output post-processing**: The full response is assembled inside `_persist_model_response()` (agent.py:156), but the session-layer loop (session.py:174-179) only sees chunks one by one; no interception hook exists

### C. Missing (must be built)

- `src/policy/types.py` — `FSMState`, `PolicyContext`, `QuestionPlan`, `PolicyDecision`
- `src/policy/fsm.py` — `PolicyFSM` with state transition logic
- `src/policy/question_bank.py` — `QUESTION_BANK` dict + `select_question()` function
- `src/policy/rules/base.py` — `BaseRule` abstract class
- `src/policy/rules/no_direct_answers.py` — `NoDirectAnswersRule`
- `src/policy/rules/tone_by_confidence.py` — `ToneByConfidenceRule`
- `src/policy/interceptors/base.py` — `BaseOutputInterceptor` abstract class
- `src/policy/interceptors/direct_answer_detector.py` — `DirectAnswerDetectorInterceptor`
- `src/policy/engine.py` — `PolicyEngine` orchestrator
- Policy state fields in `ChatSession` (`_fsm_state`, `_recent_question_ids`, `_last_question_text`)
- `prompt_directives` parameter in `OrchestratorAgent.process_session_stream()`
- Output accumulation + interception call in `ChatSession._process_turn()`
- Policy metadata in `{"type": "done", "policy": {...}}` WebSocket frame
- `tests/policy/test_fsm.py`, `test_rules.py`, `test_interceptor.py`, `test_question_bank.py`

### D. Refactors needed (before or during implementation)

- `src/adapters/llm/gemini.py:13-31` — `SYSTEM_INSTRUCTION` has a comment noting it should not be hardcoded there. It should be moved to `milo_base_context.md` or passed in during adapter construction. This is a **pre-existing technical debt item** but not a blocker; document it and leave it for a follow-on.
- `src/api/session.py:22-40` — `run_llm_evaluator()` has a `TODO` comment noting the real `MetricsEvaluator` call is not yet connected. Independent of this work; do not touch.

---

## 4. Proposed Design

### 4.1 Module location

All policy code goes under `src/policy/`. This is consistent with how `src/services/`, `src/adapters/`, and `src/metrics/` are organized — one directory per domain.

```
src/policy/
├── __init__.py
├── types.py              # All Pydantic models and enums
├── engine.py             # PolicyEngine — public surface
├── fsm.py                # PolicyFSM — state transitions
├── question_bank.py      # QUESTION_BANK + select_question()
├── rules/
│   ├── __init__.py
│   ├── base.py
│   ├── no_direct_answers.py
│   └── tone_by_confidence.py
└── interceptors/
    ├── __init__.py
    ├── base.py
    └── direct_answer_detector.py
```

### 4.2 Types (`src/policy/types.py`)

```python
import enum
from typing import Literal
from pydantic import BaseModel, Field

class FSMState(str, enum.Enum):
    PLANNING   = "PLANNING"
    MONITORING = "MONITORING"
    EVALUATION = "EVALUATION"

class UserSignals(BaseModel):
    confidence: int = Field(default=3, ge=1, le=5)
    # v1: always 3 (neutral); extend later with client signal extraction

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
    # list of interceptor names to run post-LLM
    interceptors: list[str] = Field(default_factory=list)
```

**Why Pydantic?** Every other schema in the repo uses Pydantic V2 (`ChatRequest`, `ChatResponse`, `ActivityCreate`, `MetricResult`, etc.). Consistency.

### 4.3 FSM (`src/policy/fsm.py`)

States: `PLANNING → MONITORING → EVALUATION`

Transition thresholds (tunable constants at module top):

```
PLANNING_TO_MONITORING_TURN   = 2   # after 2 completed turns, move to MONITORING
MONITORING_TO_EVALUATION_TURN = 6   # after 6 turns, move to EVALUATION
EVALUATION_RESET_TURN         = 10  # after 10 turns, reset to PLANNING (new cycle)
HIGH_CONFIDENCE_THRESHOLD     = 4   # confidence >= 4 accelerates MONITORING→EVALUATION
LOW_CONFIDENCE_THRESHOLD      = 2   # confidence <= 2 forces back to PLANNING from EVALUATION
```

Turn count is the number of completed exchange pairs (user+model), derived from `len(history) // 2` at turn start. This avoids adding a separate counter field; `get_history()` already returns the full ordered list.

**Interface:**
```python
class PolicyFSM:
    def transition(self, ctx: PolicyContext) -> FSMState:
        """Pure function — no side effects, returns next state."""
```

**Justification for counting from history**: `ChatHistoryRepository.get_history()` (chat_history.py:27) returns messages ordered chronologically from DB. `len(history) // 2` gives completed turns. Since history is loaded fresh each turn (agent.py:98), this is always accurate.

### 4.4 Question selection (`src/policy/question_bank.py`)

```python
QUESTION_BANK: dict[FSMState, list[tuple[str, str]]] = {
    FSMState.PLANNING: [
        ("plan_01", "¿Qué es lo que querés lograr en esta sesión?"),
        ("plan_02", "¿Cómo se ve el éxito para vos en este tema?"),
        ("plan_03", "¿Qué estrategia pensás usar para empezar?"),
        ("plan_04", "¿Qué parte de esto te resulta más difícil de definir?"),
        ("plan_05", "¿Qué necesitás clarificar antes de avanzar?"),
        ("plan_06", "¿Cómo medirías si estás progresando?"),
    ],
    FSMState.MONITORING: [...],   # 6 questions
    FSMState.EVALUATION: [...],   # 5 questions
}

def select_question(state: FSMState, recent_ids: list[str]) -> tuple[str, str]:
    """Return (question_id, question_text). Never raises."""
    candidates = QUESTION_BANK[state]
    for qid, qtext in candidates:
        if qid not in recent_ids:
            return qid, qtext
    # Fallback: return the least recently asked (first in list)
    return candidates[0]
```

**Why avoid repeating questions**: The `recent_question_ids` list is held in `ChatSession` in memory for the session lifetime. No DB changes needed.

### 4.5 Rules (`src/policy/rules/`)

**BaseRule** (`rules/base.py`):
```python
from abc import ABC, abstractmethod
from src.policy.types import PolicyContext, QuestionPlan

class BaseRule(ABC):
    @abstractmethod
    def apply(self, ctx: PolicyContext, plan: QuestionPlan) -> str | None:
        """Mutate plan in-place. Return rule name if it fired, None otherwise."""
```

**NoDirectAnswersRule** (`rules/no_direct_answers.py`):
- Detects trigger phrases in `ctx.user_message` (Spanish + English patterns)
- On trigger: sets `plan.constraints.forbid_direct_answer = True`, `plan.constraints.must_ask_question = True`, appends directive to `plan.prompt_directives`
- Returns `"no_direct_answers"` if fired

**ToneByConfidenceRule** (`rules/tone_by_confidence.py`):
- Maps `ctx.user_signals.confidence` to tone and optional directive
- `<= 2` → `"supportive"` + warm tone directive; `3` → `"neutral"`; `>= 4` → `"challenging"` + challenge directive
- Returns `"tone_by_confidence"` if it modified the plan

**Why two rules for v1**: These are the minimal guardrails that enforce Milo's core contract (no direct answers) and the stated adaptive behavior (tone by confidence). Both are independently testable without any LLM.

### 4.6 Output interceptor (`src/policy/interceptors/`)

**BaseOutputInterceptor** (`interceptors/base.py`):
```python
from abc import ABC, abstractmethod

class BaseOutputInterceptor(ABC):
    name: str

    @abstractmethod
    def process(self, llm_output: str, question_text: str) -> tuple[bool, str]:
        """Return (was_modified, final_text)."""
```

**DirectAnswerDetectorInterceptor** (`interceptors/direct_answer_detector.py`):
- Scans complete LLM output for patterns indicating a direct answer was given
- Checks: (a) output contains no `"?"`, (b) output starts with a known direct-answer pattern
- If violation: appends `"\n\n" + question_text` to the output, returns `(True, corrected_text)`
- Returns `(False, original_text)` if clean

**Why post-stream only**: The interceptor needs the complete response. Since `_stream_and_persist()` already collects all chunks into `collected: List[str]` (agent.py:142), the complete text is available. However, the session layer only sees individual chunks. The solution: accumulate chunks in `ChatSession._process_turn()` and run the interceptor after the stream completes (before sending `"done"`).

### 4.7 PolicyEngine (`src/policy/engine.py`)

```python
_fsm = PolicyFSM()
_rules: list[BaseRule] = [NoDirectAnswersRule(), ToneByConfidenceRule()]
_interceptors: dict[str, BaseOutputInterceptor] = {
    "direct_answer_detector": DirectAnswerDetectorInterceptor(),
}

class PolicyEngine:
    def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        next_state = _fsm.transition(ctx)
        qid, qtext = select_question(next_state, ctx.recent_question_ids)
        plan = QuestionPlan(
            question_id=qid,
            question_text=qtext,
            constraints=ResponseConstraints(),
        )
        applied = []
        for rule in _rules:
            result = rule.apply(ctx, plan)
            if result:
                applied.append(result)
        return PolicyDecision(
            next_state=next_state,
            plan=plan,
            applied_rules=applied,
            interceptors=["direct_answer_detector"],
        )

    def check_output(self, raw: str, decision: PolicyDecision) -> tuple[bool, str]:
        text = raw
        was_modified = False
        for name in decision.interceptors:
            if interceptor := _interceptors.get(name):
                modified, text = interceptor.process(text, decision.plan.question_text)
                if modified:
                    was_modified = True
        return was_modified, text
```

Module-level singletons (`_fsm`, `_rules`, `_interceptors`) are stateless — safe to share across requests. All mutable state is in `PolicyContext` (caller-owned).

---

## 5. State Ownership

### Client vs. server state

| State | Owner | Persistence |
|---|---|---|
| FSM state (`_fsm_state`) | `ChatSession` (in-memory) | Lifetime of WebSocket connection |
| Recent question IDs | `ChatSession` (in-memory) | Lifetime of WebSocket connection |
| Last question text | `ChatSession` (in-memory) | Current turn only |
| Turn count | Derived from `len(history) // 2` | DB (chat_messages) |
| User confidence | Default=3 (v1) | Not stored |
| Policy decision metadata | `ChatSession` → `"done"` frame | Client receives it, not persisted server-side |

### Why in-memory for FSM state

The `ChatSession` object lives for the full WebSocket connection. If the client disconnects and reconnects (new WebSocket), a new `ChatSession` and new DB session are created — the FSM resets to `PLANNING`, which is correct pedagogical behavior (new session = new learning cycle).

Storing FSM state in DB (e.g., as a column on `chat_sessions`) would be needed only if sessions are meant to be resumed across disconnections. That is not the current behavior and not a v1 requirement.

### Avoiding unsafe shared mutable state

- `PolicyEngine` is stateless — instantiated once, shared across sessions safely
- `PolicyContext` is constructed fresh each turn from `ChatSession`'s fields and the loaded history
- `QuestionPlan` is constructed fresh inside `evaluate()` and not shared
- `ChatSession._recent_question_ids` is a list on the instance — one per connection, no sharing

---

## 6. Integration Points (What to Modify)

### 6.1 `src/api/session.py` — `ChatSession`

**Add to `__init__`:**
```python
from src.policy.types import FSMState
from src.policy.engine import PolicyEngine

_policy_engine = PolicyEngine()  # module-level singleton

# In __init__:
self._fsm_state: FSMState = FSMState.PLANNING
self._recent_question_ids: list[str] = []
self._last_question_text: str = ""
```

**Modify `_process_turn()`:**
```python
async def _process_turn(self, user_text: str) -> None:
    # 1. Load history to derive turn count
    async with get_db_session() as db:
        history = await self._agent.history_repo.get_history(db, self._user_id, self._session_id)

    turn_count = len(history) // 2

    # 2. Evaluate policy
    ctx = PolicyContext(
        current_state=self._fsm_state,
        turn_count=turn_count,
        recent_question_ids=self._recent_question_ids.copy(),
        user_message=user_text,
    )
    decision = _policy_engine.evaluate(ctx)

    # 3. Stream with directives injected
    accumulated: list[str] = []
    async with get_db_session() as db:
        stream = self._agent.process_session_stream(
            db, self._user_id, self._session_id, user_text,
            self._context_description,
            prompt_directives=decision.plan.prompt_directives,
        )
        async for chunk in stream:
            accumulated.append(chunk)
            if not await self._send_json({"type": "chunk", "text": chunk}):
                return

    # 4. Output interception (on accumulated full text)
    full_response = "".join(accumulated)
    was_intercepted, final_text = _policy_engine.check_output(full_response, decision)
    if was_intercepted:
        correction = final_text[len(full_response):]  # only the appended part
        await self._send_json({"type": "chunk", "text": correction})

    # 5. Update session state
    self._fsm_state = decision.next_state
    self._recent_question_ids.append(decision.plan.question_id)
    self._last_question_text = decision.plan.question_text

    # 6. Done with policy metadata
    await self._send_json({
        "type": "done",
        "policy": {
            "state": decision.next_state.value,
            "question_id": decision.plan.question_id,
            "applied_rules": decision.applied_rules,
        }
    })
```

**Note**: The greeting turn (empty `user_text`) still goes through policy evaluation. `turn_count=0` will put it in PLANNING state, which is correct — the first question should be a planning question.

**Note on the extra DB call**: `_process_turn` currently opens one `get_db_session()` context for the whole stream. Adding a pre-call to load history means two DB opens per turn. This is acceptable for v1. If latency is a concern, history can be passed down from `process_session_stream` as a return value in a future refactor.

### 6.2 `src/orchestration/agent.py` — `OrchestratorAgent`

**Modify `process_session_stream` signature:**
```python
async def process_session_stream(
    self,
    db: AsyncSession,
    user_id: str,
    session_id: str,
    query: str,
    context_description: Optional[str] = None,
    prompt_directives: Optional[list[str]] = None,   # NEW
) -> AsyncIterator[str]:
```

**Modify `_compose_context` call:**
```python
context_chunks = self._compose_context(
    rag_chunks, cross_chat_memory, context_description,
    prompt_directives=prompt_directives or []          # NEW
)
```

**Modify `_compose_context`:**
```python
def _compose_context(
    self,
    rag_chunks: List[str],
    cross_chat_memory: Optional[List[Dict[str, str]]] = None,
    context_description: Optional[str] = None,
    prompt_directives: Optional[list[str]] = None,     # NEW
) -> List[str]:
    chunks: List[str] = [self.base_context]
    if context_description:
        chunks.append(f"The student is reflecting on: {context_description}")
    memory_block = self._format_memory_block(cross_chat_memory or [])
    if memory_block:
        chunks.append(memory_block)
    chunks.extend(rag_chunks)
    if prompt_directives:                               # NEW
        chunks.append("\n".join(prompt_directives))    # Injected last for salience; populated by PolicyEngine.evaluate()
    return chunks
```

No other changes to `agent.py`. The `generate_answer_stream` in `gemini.py` is untouched.

---

## 7. Testing Plan

Test files location: `tests/policy/` (recreate source .py files — compiled bytecode confirms they were designed, structure is known).

### `tests/policy/test_fsm.py`

```python
def test_planning_stays_planning_early():
    ctx = make_ctx(state=FSMState.PLANNING, turn_count=1)
    assert PolicyFSM().transition(ctx) == FSMState.PLANNING

def test_planning_to_monitoring_at_threshold():
    ctx = make_ctx(state=FSMState.PLANNING, turn_count=2)
    assert PolicyFSM().transition(ctx) == FSMState.MONITORING

def test_monitoring_to_evaluation_at_threshold():
    ctx = make_ctx(state=FSMState.MONITORING, turn_count=6)
    assert PolicyFSM().transition(ctx) == FSMState.EVALUATION

def test_high_confidence_accelerates_to_evaluation():
    ctx = make_ctx(state=FSMState.MONITORING, turn_count=3, confidence=5)
    assert PolicyFSM().transition(ctx) == FSMState.EVALUATION

def test_evaluation_resets_to_planning():
    ctx = make_ctx(state=FSMState.EVALUATION, turn_count=10)
    assert PolicyFSM().transition(ctx) == FSMState.PLANNING

def test_low_confidence_from_evaluation_resets():
    ctx = make_ctx(state=FSMState.EVALUATION, turn_count=7, confidence=1)
    assert PolicyFSM().transition(ctx) == FSMState.PLANNING
```

### `tests/policy/test_question_bank.py`

```python
def test_selects_unasked_question():
    qid, _ = select_question(FSMState.PLANNING, [])
    assert qid == "plan_01"

def test_skips_asked_questions():
    qid, _ = select_question(FSMState.PLANNING, ["plan_01"])
    assert qid == "plan_02"

def test_fallback_when_all_asked():
    all_ids = [q[0] for q in QUESTION_BANK[FSMState.PLANNING]]
    qid, _ = select_question(FSMState.PLANNING, all_ids)
    assert qid == "plan_01"  # falls back to first
```

### `tests/policy/test_rules.py`

```python
def test_no_direct_answers_triggers_on_spanish():
    plan = make_plan()
    NoDirectAnswersRule().apply(make_ctx(msg="dame la respuesta"), plan)
    assert plan.constraints.forbid_direct_answer
    assert len(plan.prompt_directives) == 1

def test_no_direct_answers_does_not_trigger_on_neutral():
    plan = make_plan()
    NoDirectAnswersRule().apply(make_ctx(msg="tengo una duda"), plan)
    assert not plan.prompt_directives

def test_tone_supportive_on_low_confidence():
    plan = make_plan()
    ToneByConfidenceRule().apply(make_ctx(confidence=1), plan)
    assert plan.tone == "supportive"
    assert len(plan.prompt_directives) == 1

def test_tone_neutral_on_mid_confidence():
    plan = make_plan()
    ToneByConfidenceRule().apply(make_ctx(confidence=3), plan)
    assert plan.tone == "neutral"
    assert not plan.prompt_directives

def test_tone_challenging_on_high_confidence():
    plan = make_plan()
    ToneByConfidenceRule().apply(make_ctx(confidence=5), plan)
    assert plan.tone == "challenging"
```

### `tests/policy/test_interceptor.py`

```python
def test_no_violation_passes_through():
    i = DirectAnswerDetectorInterceptor()
    ok, text = i.process("¿Qué estrategia pensás usar?", "¿Cómo vas con eso?")
    assert not ok
    assert text == "¿Qué estrategia pensás usar?"

def test_direct_answer_appends_question():
    i = DirectAnswerDetectorInterceptor()
    modified, text = i.process("La respuesta es 42.", "¿Qué aprendiste de esto?")
    assert modified
    assert "¿Qué aprendiste de esto?" in text

def test_no_question_mark_triggers_interceptor():
    i = DirectAnswerDetectorInterceptor()
    modified, text = i.process("El resultado es X, Y, Z.", "¿Cómo llegaste a eso?")
    assert modified
```

### Integration test (`tests/policy/test_engine.py`)

A test that constructs a `PolicyEngine`, calls `evaluate()` with a variety of `PolicyContext` inputs, verifies `next_state`/`question_id`/`applied_rules`, calls `check_output()` with a synthetic LLM response, and asserts the final output — without any LLM or DB.

---

## 8. Observability

Log policy decisions at the `session.py` layer (where ChatSession has session context):

```python
logger.info(
    "Session '%s': policy — %s→%s, q=%s, rules=%s",
    self._session_id,
    ctx.current_state.value,
    decision.next_state.value,
    decision.plan.question_id,
    decision.applied_rules,
)
```

Log interceptor fires:
```python
if was_intercepted:
    logger.info(
        "Session '%s': interceptor fired — correction appended.", self._session_id
    )
```

Follow the existing logging convention: `logging.getLogger("milo-orchestrator.{module}")` — use `"milo-orchestrator.session"` (already set up in session.py).

Do NOT log `user_message` content in policy logs — it may contain PII. Log only decision metadata.

---

## 9. Risks / Open Questions

### R1: Turn count derivation via history length
`turn_count = len(history) // 2` works while the session is active. However, if the DB query fails or history is truncated (limit=50 in `get_history`), turn count could be incorrect. For v1 this is acceptable. If a long session exceeds 50 messages, the FSM will miscount. **Mitigation**: document the assumption; consider raising the history limit or storing turn count explicitly in a future iteration.

### R2: User confidence is always 3 (neutral) in v1
`ToneByConfidenceRule` will never fire in v1 because confidence defaults to 3. This means the tone rule is wired but inactive. **Decision**: This is intentional — the rule is built and tested, but the signal injection mechanism is deferred. Add a clear `# TODO: inject real confidence from client` comment.

### R3: Extra DB call per turn in `_process_turn`
To get `turn_count` before calling the agent, `_process_turn` needs the history length, which requires a DB query. The agent already loads history internally. This means two `get_history` calls per turn. **Mitigation for v1**: acceptable overhead (DB is local, queries are indexed). **Alternative**: expose history length via a count query, or refactor agent to return history count alongside chunks.

### R4: Interceptor operates on accumulated text, but streaming UX
If the interceptor fires, the correction chunk is sent after the stream ends. This means the client will see: all LLM tokens stream in → pause → correction text appended → `"done"`. This is visible but acceptable for v1. If UX requires seamless correction, streaming needs architectural changes (not v1 scope).

### R5: `SYSTEM_INSTRUCTION` in `gemini.py:13` is noted as misplaced
The comment says "Probably, we don't want this here!" — it duplicates some of Milo's guidance. The policy engine injects directives into context (the `[Context]` section), not the system instruction. This creates two competing instruction surfaces. **For v1**: leave it as-is. It does not break anything. **Later**: consolidate into `milo_base_context.md` and remove from gemini.py.

### R6: HTTP path (`process_query`) is not policy-integrated
`POST /chat` calls `process_query()` which is stateless and doesn't go through `ChatSession`. Policy Engine is not invoked there. This is intentional for v1 — the HTTP path is not used for pedagogical interactions (it has no session, no history). Document this explicitly.

### R7: `tests/policy/*.py` source files were deleted
Only `.pyc` bytecode exists. The test structures described in section 7 are reconstructed from the prior branch exploration. When writing tests, use those structures as reference but verify against the final implementation.

---

## 10. First Implementation Slice

The safest first slice is **pure logic, no integration**:

1. `src/policy/__init__.py` — empty, establishes module
2. `src/policy/types.py` — all Pydantic types and enums
3. `src/policy/fsm.py` — `PolicyFSM.transition()` (pure function, fully testable)
4. `src/policy/question_bank.py` — `QUESTION_BANK` + `select_question()` (pure function)
5. `tests/policy/test_fsm.py` — all FSM transition tests pass
6. `tests/policy/test_question_bank.py` — all question selection tests pass

This slice has **zero integration risk** — no existing files are modified. All logic is fully deterministic and unit-testable. It proves the FSM and question selection work correctly before wiring anything into the orchestrator.

Second slice: rules + interceptors + engine, with tests.

Third slice: integration into `ChatSession` and `OrchestratorAgent`, with integration tests.

---

## 11. Phased Implementation Plan

Each phase is self-contained: it has a clear goal, concrete steps, tests that must pass before moving forward, and documentation to update. **Do not start a later phase until all tests in the current phase are green.**

---

### Phase 1 — Core Types and Pure Logic (no integration)

**Goal**: Establish the policy module with all pure, deterministic logic. Zero risk — no existing file is modified.

**Steps**:
1. Create `src/policy/__init__.py` (empty)
2. Create `src/policy/types.py` — `FSMState`, `UserSignals`, `PolicyContext`, `ResponseConstraints`, `QuestionPlan`, `PolicyDecision`
3. Create `src/policy/fsm.py` — `PolicyFSM` with module-level threshold constants and `transition()` method
4. Create `src/policy/question_bank.py` — `QUESTION_BANK` dict (all 17 questions across 3 states) and `select_question()` function
5. Create `tests/__init__.py` if missing; create `tests/policy/__init__.py`
6. Create `tests/policy/test_fsm.py` — test all transition edges (thresholds, confidence acceleration, reset)
7. Create `tests/policy/test_question_bank.py` — test selection, skipping, fallback

**Testing** (must pass before Phase 2):
- `pytest tests/policy/test_fsm.py` — all transition cases green
- `pytest tests/policy/test_question_bank.py` — all selection cases green
- No imports from outside `src/policy/`

**Documentation**:
- Add docstrings to `PolicyFSM.transition()` explaining each threshold constant
- Add a module-level comment in `question_bank.py` noting the question set is in Spanish and explaining the selection algorithm

---

### Phase 2 — Rules and Interceptors

**Goal**: Build the pluggable rule system and output interceptor. Still purely logic — no integration, no LLM.

**Steps**:
1. Create `src/policy/rules/__init__.py`
2. Create `src/policy/rules/base.py` — `BaseRule` abstract class
3. Create `src/policy/rules/no_direct_answers.py` — `NoDirectAnswersRule` with Spanish + English trigger phrases
4. Create `src/policy/rules/tone_by_confidence.py` — `ToneByConfidenceRule` mapping confidence 1-5 to tone + directive
5. Create `src/policy/interceptors/__init__.py`
6. Create `src/policy/interceptors/base.py` — `BaseOutputInterceptor` abstract class
7. Create `src/policy/interceptors/direct_answer_detector.py` — `DirectAnswerDetectorInterceptor`
8. Create `tests/policy/test_rules.py`
9. Create `tests/policy/test_interceptor.py`

**Testing** (must pass before Phase 3):
- `pytest tests/policy/test_rules.py` — tone cases, trigger cases, no-trigger cases
- `pytest tests/policy/test_interceptor.py` — violation detected, clean output passes through, correction appended correctly
- All Phase 1 tests still green

**Documentation**:
- Docstring on `NoDirectAnswersRule` listing all trigger phrases
- Docstring on `DirectAnswerDetectorInterceptor` explaining detection heuristics and the append behavior
- Inline comment in `tone_by_confidence.py` noting that confidence defaults to 3 (neutral) in v1

---

### Phase 3 — PolicyEngine (wire it together)

**Goal**: Build the `PolicyEngine` class that orchestrates FSM, question selection, rules, and interceptors. Still no integration with the orchestrator or session.

**Steps**:
1. Create `src/policy/engine.py` — `PolicyEngine` with module-level singletons, `evaluate()`, and `check_output()`
2. Create `tests/policy/test_engine.py` — integration-style unit test that runs the full engine without any DB or LLM:
   - Call `evaluate()` with a variety of `PolicyContext` inputs
   - Assert `next_state`, `question_id`, `applied_rules`
   - Call `check_output()` with a synthetic LLM response and assert correction behavior

**Testing** (must pass before Phase 4):
- `pytest tests/policy/` — all tests from Phases 1-3 green
- Verify `PolicyEngine` is stateless: call `evaluate()` twice with identical inputs → identical output

**Documentation**:
- Docstring on `PolicyEngine` explaining the evaluation order (FSM → question selection → rules → configure interceptors)
- Docstring on `check_output()` explaining when correction is appended vs. passed through
- Inline comment at top of `engine.py` describing the two-phase lifecycle (`evaluate` pre-LLM, `check_output` post-LLM)

---

### Phase 4 — Agent Integration (pre-LLM directive injection)

**Goal**: Wire policy directives into the prompt context. Modify `OrchestratorAgent` minimally — one optional parameter, one context injection.

**Steps**:
1. Modify `src/orchestration/agent.py`:
   - Add `prompt_directives: Optional[list[str]] = None` to `process_session_stream()`
   - Add `prompt_directives: Optional[list[str]] = None` to `_compose_context()`
   - In `_compose_context()`: if `prompt_directives`, append `"\n".join(prompt_directives)` as the last context chunk
2. Write a unit test to verify that `_compose_context()` includes the directive chunk when provided and omits it when not provided — no LLM call required

**Testing** (must pass before Phase 5):
- `pytest tests/policy/` — all existing policy tests still green
- Unit test: call `_compose_context(rag_chunks=[], prompt_directives=["Test directive"])` and assert the directive appears in the returned list
- The existing HTTP path (`process_query()`) is unaffected (it does not pass directives)

**Documentation**:
- Add inline comment above the `prompt_directives` injection block in `_compose_context()`:
  `# Injected last for salience; populated by PolicyEngine.evaluate()`

---

### Phase 5 — Session Integration (full per-turn policy loop)

**Goal**: Activate the full policy loop inside `ChatSession`. This is the highest-risk phase — it touches the live streaming path.

**Steps**:
1. Modify `src/api/session.py`:
   - Import `PolicyEngine`, `PolicyContext`, `FSMState` from `src/policy`
   - Add module-level `_policy_engine = PolicyEngine()` singleton
   - Add `_fsm_state`, `_recent_question_ids`, `_last_question_text` fields to `__init__()`
   - Rewrite `_process_turn()` following the 6-step sequence in section 6.1

**Testing** (must pass before Phase 6):
- `pytest tests/policy/` — all prior tests still green
- Manual end-to-end test via WebSocket client (wscat or equivalent):
  - Connect to `ws://localhost:{PORT}/chat/activities/{activity_id}`
  - Send 3 messages; observe `"policy"` key in `"done"` frames
  - Verify state progression: first `"done"` shows `PLANNING`, after 2 turns shows `MONITORING`
  - Send a message containing "dame la respuesta"; observe `applied_rules` includes `"no_direct_answers"`
- Verify greeting turn (empty query) still works — FSM starts at PLANNING

**Documentation**:
- Add a comment block above `_process_turn()` describing the policy loop steps
- Document the updated WebSocket protocol: the `"done"` frame now carries a `"policy"` key

---

### Phase 6 — Observability and Hardening

**Goal**: Add structured logging for policy decisions, guard against edge cases, and confirm the full system is production-ready.

**Steps**:
1. Add policy decision log in `session.py` after `evaluate()` (see section 8 for exact format)
2. Add interceptor fire log after `check_output()`
3. Add a guard in `_process_turn()`: if `evaluate()` raises unexpectedly, log the error and continue with empty directives — degrade gracefully, do not fail the turn
4. Confirm the greeting turn (turn_count=0, user_message="") does not trigger `NoDirectAnswersRule`

**Testing** (must pass before shipping):
- `pytest tests/policy/` — full suite green
- Run with `LOG_LEVEL=DEBUG` and verify policy log lines appear correctly
- Simulate a rule exception (monkeypatch): confirm the session continues and logs an error
- Run the full session lifecycle: connect → greet → 3 turns → disconnect → verify `PENDING_EVALUATION` status in DB

**Documentation**:
- Update section 8 (Observability) with the exact log format used
- Add a `# DEFERRED` comment next to the confidence=3 default in `types.py`, referencing R2

---

## 12. File-Level Checklist

### New files to create

| File | Purpose | Phase |
|---|---|---|
| `src/policy/__init__.py` | Module marker | 1 |
| `src/policy/types.py` | `FSMState`, `PolicyContext`, `QuestionPlan`, `PolicyDecision`, `UserSignals`, `ResponseConstraints` | 1 |
| `src/policy/fsm.py` | `PolicyFSM` with transition thresholds | 1 |
| `src/policy/question_bank.py` | `QUESTION_BANK` dict + `select_question()` | 1 |
| `tests/policy/__init__.py` | Module marker | 1 |
| `tests/policy/test_fsm.py` | FSM transition tests | 1 |
| `tests/policy/test_question_bank.py` | Question selection tests | 1 |
| `src/policy/rules/__init__.py` | Module marker | 2 |
| `src/policy/rules/base.py` | `BaseRule` abstract class | 2 |
| `src/policy/rules/no_direct_answers.py` | `NoDirectAnswersRule` | 2 |
| `src/policy/rules/tone_by_confidence.py` | `ToneByConfidenceRule` | 2 |
| `src/policy/interceptors/__init__.py` | Module marker | 2 |
| `src/policy/interceptors/base.py` | `BaseOutputInterceptor` abstract class | 2 |
| `src/policy/interceptors/direct_answer_detector.py` | `DirectAnswerDetectorInterceptor` | 2 |
| `tests/policy/test_rules.py` | Rule tests (tone, no-direct-answers) | 2 |
| `tests/policy/test_interceptor.py` | Output interception tests | 2 |
| `src/policy/engine.py` | `PolicyEngine.evaluate()` + `check_output()` | 3 |
| `tests/policy/test_engine.py` | End-to-end engine tests (no LLM/DB) | 3 |

### Existing files to modify

| File | Change | Phase |
|---|---|---|
| `src/orchestration/agent.py` | Add optional `prompt_directives` param to `process_session_stream()` and `_compose_context()` | 4 |
| `src/api/session.py` | Add FSM state fields; rewrite `_process_turn()` with policy loop; update `"done"` payload | 5 |

### Existing files NOT to touch

| File | Reason |
|---|---|
| `src/adapters/llm/gemini.py` | No changes needed; `SYSTEM_INSTRUCTION` debt is deferred |
| `src/core/models.py` | No new DB columns needed in v1 (FSM state is in-memory) |
| `src/services/metrics_evaluator.py` | Post-session evaluation is independent of per-turn policy |
| `src/api/routers/chat.py` | HTTP path is out of scope for v1 policy integration |
| `src/prompts/milo_base_context.md` | Leave as-is; policy directives are injected dynamically, not baked in |
