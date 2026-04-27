# Policy Engine v3 — Phased Implementation Spec

## 1. Context

The current Policy Engine ([src/policy/](milo-back-agent-orchestrator/src/policy/)) ships a working two-phase orchestrator (FSM, question bank, two rules, one interceptor) with 53 green tests. The deep-research report ([src/policy/deep-research-report.md](milo-back-agent-orchestrator/src/policy/deep-research-report.md)) exposes a structural gap: the engine treats every learner identically because **`UserSignals.confidence` is hardcoded to `3` (neutral)** ([src/policy/types.py:16](milo-back-agent-orchestrator/src/policy/types.py#L16)). `ToneByConfidenceRule` therefore never fires, the FSM never accelerates/resets via confidence, and there is no other learner-state signal feeding the engine.

Beyond that dead weight, the engine lacks constructs the SRL/ITS literature treats as table stakes: derived scores (struggle, miscalibration, hint-abuse), a graduated hint ladder, a confusion-recovery micro-state, a meta-feedback cooldown, tagged question families, activity-context awareness, and persistence across reconnects. The output interceptor is bypassable (a single rhetorical `"?"` defeats it), and the input side has no attempt-elicitation route for direct-answer requests.

This spec sequences six phases that turn the placebo half of the engine into real signal-driven behavior, harden the guardrails against adversarial inputs, and add research-grounded pedagogical depth — under single-developer / thesis constraints. The 53 existing tests must stay green throughout; each phase ships independently.

**User-stated priorities:** robustness (guardrails that hold under adversarial inputs) and pedagogical richness (deeper, research-backed coaching).

---

## 2. Implementation Notes

- **Each phase is independently shippable.** If timeline tightens, stop after any completed phase; the engine is strictly better than today at that point.
- **TDD-friendly**: every new component has a test file specified. Suggested cadence: write failing test → implement → green → integrate.
- **No frontend changes** in any phase. Signals are derived from text + server-side timing only.
- **Backwards compatibility**: `UserSignals` extensions are additive; existing tests must keep constructing it without args. Phase 6 is the only phase that intentionally breaks tests (and rewrites them in-place).
- **Research traceability**: each phase cites which directive(s) from the report it satisfies.

---

## 3. Phased Plan

### Dependency chain

```
Phase 1 (signals + scores) — foundation for all richness
   ├─→ Phase 3 (question selector reads scores)
   ├─→ Phase 4 (hint ladder + recovery read scores)
   └─→ Phase 6 (FSM reads scores instead of confidence)

Phase 2 (robustness) — uses Phase 1 signals; can ship in parallel after P1

Phase 3 (families) ──→ Phase 4 (recovery family must exist)

Phase 4 (state machines on context) ──→ Phase 5 (persistence captures them)

Phase 5 (persistence) ──→ Phase 6 (acceptance scenarios test reconnect)
```

---

### Phase 1 — Signal Extraction & Score Registry  ·  Priority: **P0 (critical)**

**Goal**: Replace the hardcoded `confidence=3` with multi-dimensional `UserSignals` populated each turn from text + server-derivable features, and compute relative `Scores` from a rolling window.

**What it adds** (new files in `/policy/`):
- [src/policy/signals/__init__.py](milo-back-agent-orchestrator/src/policy/signals/__init__.py)
- [src/policy/signals/extractors.py](milo-back-agent-orchestrator/src/policy/signals/extractors.py) — pure functions: `extract_hedging`, `extract_confusion_keywords`, `extract_attempt_presence`, `extract_direct_answer_request`, `extract_message_length_z`, `extract_revision_markers`, `extract_latency_z`. Bilingual ES/EN lexicons.
- [src/policy/signals/aggregator.py](milo-back-agent-orchestrator/src/policy/signals/aggregator.py) — `build_user_signals(user_message, history, prev_milo_response_ts, now_ts) -> UserSignals`.
- [src/policy/scores.py](milo-back-agent-orchestrator/src/policy/scores.py) — `Scores` Pydantic model (`struggle, miscalibration, hint_abuse, help_avoidance, affect_load`, all 0.0–1.0). `compute_scores(window, current) -> Scores` using rolling z-scores / percentiles, never absolute seconds.
- Tests: `tests/policy/test_signal_extractors.py`, `test_signal_aggregator.py`, `test_scores.py` — ~25-30 unit tests.

**What it changes**:
- [src/policy/types.py](milo-back-agent-orchestrator/src/policy/types.py) — extend `UserSignals` (additive, all defaults safe-neutral): `hedging: float = 0.0`, `confusion: float = 0.0`, `attempt_present: bool = True`, `direct_answer_request: bool = False`, `latency_z: float = 0.0`, `length_z: float = 0.0`, `revisions: int = 0`. Keep `confidence` for now (Phase 6 removes). Add `PolicyContext.signals_window: list[UserSignals]` and `PolicyContext.scores: Optional[Scores] = None`.
- [src/policy/engine.py:40-67](milo-back-agent-orchestrator/src/policy/engine.py#L40-L67) — at top of `evaluate()`, compute `ctx.scores = compute_scores(...)` before FSM/rules.
- [src/api/session.py:60-63, 202-208](milo-back-agent-orchestrator/src/api/session.py#L60-L63) — add `_signals_window` (capped at 10) and `_last_milo_response_ts`. Build signals before `PolicyContext`. Update timestamp after stream completes.
- [src/policy/rules/tone_by_confidence.py](milo-back-agent-orchestrator/src/policy/rules/tone_by_confidence.py) — switch from raw `confidence` to score-driven: supportive when `affect_load > 0.6`, challenging when `miscalibration > 0.6 AND hedging < 0.2`. Update tests accordingly. Delete the misleading `# DEFERRED (R2)` comment in [types.py:14-15](milo-back-agent-orchestrator/src/policy/types.py#L14-L15).

**Acceptance criteria**:
- 53 existing tests still green (additive defaults preserve behavior).
- New tests assert: hedging detected on "creo que tal vez no sé"; confusion on "no entiendo nada"; `attempt_present=False` on "dame la respuesta"; `latency_z > 1.5` when current latency > p90 of window; `Scores.struggle` rises monotonically with hedging+latency.
- Manual: 5-message hedging session shows rising `Scores.struggle` in logs.
- `ToneByConfidenceRule` fires at least once in a hedging-heavy integration test.

**Research citation**: Winne & Hadwin trace data; Koedinger & Aleven on relative thresholds; D'Mello & Graesser confusion lexicon.

---

### Phase 2 — Robust Output Interceptor + Input Attempt Elicitation  ·  Priority: **P0 (critical)**

**Goal**: Close the two adversarial holes — rhetorical `"?"` bypass on output, and direct-answer requests with no attempt on input.

**What it adds**:
- [src/policy/interceptors/rhetorical_question_detector.py](milo-back-agent-orchestrator/src/policy/interceptors/rhetorical_question_detector.py) — detects assertion-then-rhetorical-question. Heuristics: `?` is in last 15% of text AND text starts with non-question sentence ≥ 8 words AND question matches closed-form patterns (`¿Entendiste?`, `¿Está claro?`, `¿Tiene sentido?`, `¿Ok?`, `¿No?`).
- [src/policy/interceptors/open_endedness_classifier.py](milo-back-agent-orchestrator/src/policy/interceptors/open_endedness_classifier.py) — pure rule-based scorer; closed-form yes/no scores < 0.3, wh-questions score > 0.6.
- [src/policy/rules/elicit_attempt.py](milo-back-agent-orchestrator/src/policy/rules/elicit_attempt.py) — `ElicitAttemptRule`: fires when `direct_answer_request AND not attempt_present`. Sets `must_elicit_attempt=True`, replaces planned question with one from `ATTEMPT_ELICITATION` family.
- Tests: `test_rhetorical_interceptor.py`, `test_open_endedness.py`, `test_elicit_attempt.py` — adversarial cases (10 should-fire, 10 should-pass).

**What it changes**:
- [src/policy/interceptors/direct_answer_detector.py:32-40](milo-back-agent-orchestrator/src/policy/interceptors/direct_answer_detector.py#L32-L40) — replace permissive `"?" in llm_output` with `any(open_endedness_score(s) > 0.5 for s in split_sentences(llm_output))`. Add position check: if only `?` is in last 15% after long assertive prefix, defer to rhetorical detector.
- [src/policy/types.py](milo-back-agent-orchestrator/src/policy/types.py) — add `ResponseConstraints.must_elicit_attempt: bool = False`.
- [src/policy/engine.py:28-31](milo-back-agent-orchestrator/src/policy/engine.py#L28-L31) — register new rule and interceptor; default interceptor list now includes `"rhetorical_question_detector"`.

**Acceptance criteria**:
- 20-case adversarial test suite all green.
- "Sí, eso está bien. ¿Entendiste?" must trigger interceptor; "¿Qué crees que pasaría si...?" must pass.
- "dame la respuesta" with no prior attempt → response asks "¿Qué probaste?"-family question, `applied_rules: ["elicit_attempt"]`.

**Research citation**: Aleven & Koedinger help-seeking model; Graesser & Person question taxonomies; Narciss informative tutoring feedback.

---

### Phase 3 — Question Families + Activity Contextualization  ·  Priority: **P1 (high)**

**Goal**: Replace flat question bank with tagged questions in named pedagogical families, and inject `teacher_goal` + `context_description` into planned questions.

**What it adds**:
- [src/policy/questions/__init__.py](milo-back-agent-orchestrator/src/policy/questions/__init__.py)
- [src/policy/questions/families.py](milo-back-agent-orchestrator/src/policy/questions/families.py) — `QuestionFamily` enum: `GOAL_CLARIFICATION`, `SELF_EXPLANATION`, `CALIBRATION`, `DISCREPANCY_DETECTION`, `TRANSFER`, `REATTRIBUTION`, `MONITORING_CHECK`, `STRATEGY_REVISION`, `ATTEMPT_ELICITATION` (used by Phase 2), `RECOVERY_STABILIZE` (used by Phase 4).
- [src/policy/questions/bank.py](milo-back-agent-orchestrator/src/policy/questions/bank.py) — `Question` Pydantic model with `id, family, state, surface_variants: list[str], tags: dict` (keys: `difficulty: 1|2|3`, `tone: supportive|neutral|challenging`, `escalation_level: 0|1|2|3`, `requires_attempt: bool`). Expand from 18 to ~35 questions.
- [src/policy/questions/selector.py](milo-back-agent-orchestrator/src/policy/questions/selector.py) — `select_question(state, family_preference, scores, recent_ids, activity) -> Question`. Scores drive family preference (e.g., `miscalibration > 0.5` → prefer `CALIBRATION` or `DISCREPANCY_DETECTION`).
- [src/policy/questions/contextualizer.py](milo-back-agent-orchestrator/src/policy/questions/contextualizer.py) — `contextualize(question_text, activity) -> str`. Templating (`{topic}` → `activity.context_description`); pure string ops, no LLM.
- Tests: `test_question_families.py`, `test_question_selector.py`, `test_contextualizer.py`.

**What it changes**:
- [src/policy/question_bank.py](milo-back-agent-orchestrator/src/policy/question_bank.py) — keep as thin shim re-exporting `select_question` from new module (preserves existing imports in [engine.py:15](milo-back-agent-orchestrator/src/policy/engine.py#L15)).
- [src/policy/types.py](milo-back-agent-orchestrator/src/policy/types.py) — add `PolicyContext.activity: Optional[ActivityRef] = None` where `ActivityRef` is `{id, teacher_goal, context_description}`.
- [src/policy/engine.py:47-54](milo-back-agent-orchestrator/src/policy/engine.py#L47-L54) — pass `activity` and `scores` to `select_question`; call `contextualize(qtext, activity)` before constructing `QuestionPlan`.
- [src/api/session.py:108-110](milo-back-agent-orchestrator/src/api/session.py#L108-L110) — widen activity load to capture `teacher_goal` (currently only `context_description`); construct `ActivityRef` once at setup, pass into every `PolicyContext`.

**Acceptance criteria**:
- Session on activity "derivadas" produces a planning question containing the topic phrase in first 3 turns.
- Synthetic high `miscalibration` steers selection to `CALIBRATION`/`DISCREPANCY_DETECTION` (testable without LLM).
- 30+ questions tagged; no question lacks family or escalation level.

**Research citation**: Graesser & Person Q-taxonomy; Chi self-explanation; Bangert-Drowns on activity-contextualized reflection.

---

### Phase 4 — Hint Ladder + Confusion Recovery + Cooldown  ·  Priority: **P1 (high)**

**Goal**: Add the assistance-dilemma machinery — graduated escalation, recovery from confusion, and rate-limited meta-feedback.

**What it adds**:
- [src/policy/hint_ladder.py](milo-back-agent-orchestrator/src/policy/hint_ladder.py) — `HintLadder` with states `PROCESS_FEEDBACK → STRATEGIC_HINT → FOCUSED_HINT → BOTTOM_OUT`. Advance one rung per turn when `struggle > 0.6 AND attempt_present`. Reset to `PROCESS_FEEDBACK` after 2 turns of `struggle < 0.3`. **Bottom-out reachable only after ≥ 3 turns at FOCUSED_HINT** — never first-line.
- [src/policy/recovery.py](milo-back-agent-orchestrator/src/policy/recovery.py) — `RecoveryState` enum (`NORMAL`, `STABILIZE`). Enter when `confusion > 0.5 AND affect_load > 0.5 AND repeated_error_marker`. While in `STABILIZE`: question selector forced to `RECOVERY_STABILIZE` family (validation + narrowed-choice); FSM transitions paused; bottom-out blocked. Cap at 4 turns; on exit, force-advance one FSM step.
- [src/policy/cooldown.py](milo-back-agent-orchestrator/src/policy/cooldown.py) — `MetaFeedbackCooldown` tracking `turns_since_meta_feedback`. Returns False for non-essential interventions (tone change, redundant rule fires) when `turns_since_meta_feedback < 2`. Always True for essential (direct-answer leak block, attempt elicitation, recovery).
- [src/policy/rules/hint_ladder_rule.py](milo-back-agent-orchestrator/src/policy/rules/hint_ladder_rule.py) — reads `scores.struggle`, advances ladder, sets directives ("Process feedback only" / "Strategic hint, no specifics" / "Focused hint, name the concept" / "Bottom-out: give worked sub-step only, not final answer; end with check question").
- Tests: `test_hint_ladder.py`, `test_recovery.py`, `test_cooldown.py` — state machines tested in isolation; integration test in `test_engine.py`.

**What it changes**:
- [src/policy/types.py](milo-back-agent-orchestrator/src/policy/types.py) — add `HintLadderState`, `RecoveryState` enums. Add `PolicyContext.hint_state`, `recovery_state`, `turns_since_meta_feedback`. Add same fields to `PolicyDecision` so caller can persist them.
- [src/policy/engine.py](milo-back-agent-orchestrator/src/policy/engine.py) — `evaluate()` order: scores → recovery check → FSM → hint ladder → question selection → rules (filtered by cooldown) → interceptors.
- [src/api/session.py:60-63, 254-256](milo-back-agent-orchestrator/src/api/session.py#L60-L63) — add `_hint_state`, `_recovery_state`, `_turns_since_meta_feedback`; update from `decision` after each turn.

**Acceptance criteria**:
- Synthetic 5-turn `struggle=0.8 + attempt_present=True` walks PROCESS → STRATEGIC → FOCUSED, never reaches BOTTOM_OUT until turn 6+.
- Confusion + low confidence triggers RECOVERY; question family forced to `RECOVERY_STABILIZE`; no FSM transition during recovery.
- Cooldown test: with `turns_since_meta_feedback=1`, `ToneByConfidenceRule` suppressed but `NoDirectAnswersRule` still fires.
- All prior tests still green.

**Research citation**: Koedinger & Aleven assistance dilemma; Narciss ITF; D'Mello & Graesser confusion → stabilize; Aleven help-seeking (>75% intervention is counterproductive).

---

### Phase 5 — Persistence, Evidence Registry, and Metrics  ·  Priority: **P2 (medium)**

**Goal**: Survive reconnects, document the paper-to-rule mapping, and instrument the engine so the thesis can report numbers.

**What it adds**:
- [src/policy/persistence.py](milo-back-agent-orchestrator/src/policy/persistence.py) — `PolicyStateSnapshot` Pydantic model containing everything in-memory on `ChatSession`: `fsm_state, recent_question_ids, hint_state, recovery_state, signals_window, turns_since_meta_feedback, version: 1`. `serialize/deserialize` helpers.
- [src/policy/evidence.py](milo-back-agent-orchestrator/src/policy/evidence.py) — `EVIDENCE_REGISTRY: dict[component_name, list[Citation]]`. Each `Citation`: `{author, year, claim, source_url}`. Surfaced via debug endpoint `GET /policy/evidence` (read-only) for thesis-time inspection.
- [src/policy/metrics.py](milo-back-agent-orchestrator/src/policy/metrics.py) — counters and rolling windows: `direct_answer_leakage_rate`, `hint_distribution` (histogram across ladder rungs), `over_intervention_rate` (turns with >1 rule fire / total), `calibration_gap_proxy`. `MetricsCollector.snapshot() -> dict`.
- Tests: `test_persistence.py`, `test_metrics.py`.

**What it changes**:
- [src/core/models.py](milo-back-agent-orchestrator/src/core/models.py) (`ChatSession` model) — add column `policy_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)`. Snapshot-style; no schema migration churn.
- [src/api/session.py:89-122, 124-138](milo-back-agent-orchestrator/src/api/session.py#L89-L122) — on setup, load `policy_state` if present and rehydrate; persist after every `evaluate()`; final snapshot on wrap-up.
- [src/services/metrics_evaluator.py](milo-back-agent-orchestrator/src/services/metrics_evaluator.py) — read `MetricsCollector.snapshot()` and write into existing `SessionMetric` row (extend model with `policy_metrics: JSON nullable=True`).
- Each rule/interceptor file gets a top-of-file `__evidence__ = ["citation_key_1", ...]` constant referencing keys in the registry.

**Acceptance criteria**:
- Disconnect mid-session → reconnect → `_fsm_state` and `_recent_question_ids` resume (not reset to PLANNING).
- `GET /policy/evidence` returns ≥ 8 citations covering: FSM stages, hint ladder, recovery, cooldown, calibration questions, attempt elicitation, score formulas, rhetorical interceptor.
- Run a 10-turn session → `direct_answer_leakage_rate == 0.0`, `hint_distribution[BOTTOM_OUT] / total < 0.1`, `over_intervention_rate < 0.25`.

**Research citation**: meta — replication and traceability are the thesis-quality requirement, not a specific report directive.

---

### Phase 6 — FSM Confidence Removal + Acceptance Hardening  ·  Priority: **P2 (medium)**

**Goal**: Finish removing the `confidence=3` placebo and run the full system through an adversarial acceptance pass.

**What it changes**:
- [src/policy/fsm.py:7-8, 26, 34, 39](milo-back-agent-orchestrator/src/policy/fsm.py#L7-L8) — replace `confidence` reads with score-driven decisions:
  - Remove `HIGH_CONFIDENCE_THRESHOLD`; replace with `scores.miscalibration < 0.3 AND scores.struggle < 0.3` for accelerating MONITORING→EVALUATION.
  - Remove `LOW_CONFIDENCE_THRESHOLD`; replace with `scores.struggle > 0.7 OR scores.affect_load > 0.7` for forcing EVALUATION→PLANNING.
  - Update [tests/policy/test_fsm.py](milo-back-agent-orchestrator/tests/policy/test_fsm.py) — switch `confidence=5` assertions to `scores=Scores(miscalibration=0.1, struggle=0.1)`. **Only phase that rewrites existing tests.**
- [src/policy/types.py](milo-back-agent-orchestrator/src/policy/types.py) — remove `UserSignals.confidence` field. Verify zero remaining reads in codebase.

**What it adds**:
- [tests/policy/test_acceptance.py](milo-back-agent-orchestrator/tests/policy/test_acceptance.py) — 15-20 scripted scenarios (each is a list of `(user_message, expected_invariant)` pairs run through engine, no LLM):
  1. Five turns of "dame la respuesta" → never produces direct answer; routes through attempt elicitation.
  2. Hedging → confusion → repeated error → engine enters RECOVERY, paused FSM, narrowed-choice question.
  3. Steady high attempts, low hedging → reaches EVALUATION quickly via score-based acceleration.
  4. Rhetorical "¿Entendiste?" only → interceptor appends real Socratic question.
  5. Rate-limit: confidence dropping but `NoDirectAnswersRule` already fired → tone rule suppressed by cooldown.
  6. Reconnect mid-RECOVERY → state restored.
  7. Activity contextualization appears in question text.
  8. Hint ladder never reaches BOTTOM_OUT before turn 6.
  9-15. Edge cases: empty greeting, non-Spanish text, very long messages, repeated identical messages.

**Acceptance criteria**:
- All previous test files green plus `test_acceptance.py` 100% green.
- `grep -r "confidence" src/policy/` returns zero hits.
- Manual demo session against live WebSocket: produce a recording where (a) hedging student gets supportive tone, (b) over-confident student gets discrepancy question, (c) "give me the answer" student gets attempt elicitation.

**Research citation**: closes Winne & Hadwin (signals replace self-report) and report directive #2 (relative not absolute thresholds).

---

## 4. Out of Scope (explicitly deferred)

Mentioned in the report but NOT in any phase above. Each requires frontend work, ML, or research infrastructure that exceeds single-developer / thesis budget.

- **Frontend confidence-rating slider** — explicit Likert input from UI. Constraints exclude frontend instrumentation.
- **Hint-click telemetry** — counting hint requests via UI button events. Server-only signals approximate via `direct_answer_request` extractor.
- **Explanation-quality classifier** — ML model scoring whether a self-explanation engages with the concept. Post-session `metrics_evaluator.py` LLM grader covers this approximately at session granularity.
- **Per-student longitudinal calibration baselines** — z-scores in Phase 1 are within-session only. Cross-session baselines need learner profile schema.
- **RCT evaluation** — running our own randomized controlled trial is not the thesis's empirical contribution.
- **A/B testing framework** — multi-arm rule variants would require deployment infrastructure.
- **Multi-language detection beyond ES/EN** — extractors are bilingual; expansion is mechanical.
- **HTTP `POST /chat` policy integration** — stateless path remains policy-free.
- **Real-time streaming interception** — interceptor remains post-stream. Mid-stream cutoff is a UX gap accepted for v3.
- **`SYSTEM_INSTRUCTION` consolidation** in [src/adapters/llm/gemini.py:13](milo-back-agent-orchestrator/src/adapters/llm/gemini.py#L13) — pre-existing tech debt, untouched.

---

## 5. Verification Approach

### Per-phase unit tests

Each phase has unit tests in `tests/policy/`. Run from `milo-back-agent-orchestrator/`:

```bash
.venv/bin/python3.11 -m pytest tests/policy/ -v
```

Expected: 53 baseline tests + new tests per phase, all green at every phase boundary.

### End-to-end verification per phase

**Phase 1**: `LOG_LEVEL=DEBUG uvicorn src.main:app --reload`. Connect via `wscat -c "ws://localhost:8000/chat/activities/<id>"`. Send hedging message ("creo que tal vez no, no sé bien..."). In server logs: `signals=hedging:0.7` and `scores=struggle:0.5`. Send confident wrong message; observe `miscalibration` rise.

**Phase 2**: Send "dame la respuesta" with no prior attempt. Response asks "¿Qué probaste?"; `done` frame includes `applied_rules: ["elicit_attempt"]`. Then craft an LLM response ending with "¿Entendiste?" (mock or override); verify rhetorical interceptor appends real question.

**Phase 3**: Connect to activity titled "derivadas con regla de la cadena". First planning question contains the topic phrase. Drive miscalibration high → planning questions shift to calibration family (logged via `question_family=CALIBRATION`).

**Phase 4**: Send 5 messages of "no entiendo, dame ayuda". Logs show ladder progression `PROCESS_FEEDBACK → STRATEGIC_HINT → FOCUSED_HINT`. No `BOTTOM_OUT` before turn 6. Send confused replies; observe `recovery_state=STABILIZE` in `done` payload.

**Phase 5**: Mid-session, kill `wscat`, reconnect. `done` payload shows FSM resumed at prior state. Visit `GET /policy/evidence` in browser; verify ≥ 8 citations. After session: `SELECT policy_metrics FROM session_metrics WHERE session_id = ?` returns JSON with `direct_answer_leakage_rate, hint_distribution`.

**Phase 6**: `pytest tests/policy/test_acceptance.py -v` all green. Manual demo recording session demonstrating the 3 invariants.

### Logging additions per phase

Existing convention (`milo-orchestrator.session`) preserved. New log lines:
- Phase 1: `signals=...` and `scores=...` per turn
- Phase 2: `interceptor=rhetorical_question_detector fired` when applicable
- Phase 4: `hint_state=PROCESS→STRATEGIC` and `recovery_state=NORMAL→STABILIZE` transitions
- Phase 5: `policy_state persisted` per turn, `policy_state restored` on reconnect

PII discipline: never log `user_message` content — only decision metadata.

---

## 6. Risks & Mitigations

**R1 — Signal extraction false positives**: hedging/confusion lexicons may misfire on technical content (a math student saying "no sé qué pasa con la integral" is hedging about math, not their confidence). Mitigation: extractors return floats not booleans; `Scores` integrate over a window so single-turn noise is dampened.

**R2 — Latency z-score requires history**: first 2-3 turns of every session have no baseline. Mitigation: extractor returns 0.0 (neutral) when window length < 3; `compute_scores` weights other features more heavily early in session.

**R3 — Rule + cooldown ordering**: cooldown filtering rules creates an order-of-operations subtlety; the cooldown decrement must happen exactly once per turn. Mitigation: increment/decrement only inside `engine.evaluate()`, not inside individual rules.

**R4 — Persistence schema drift**: opaque JSON snapshots silently break on schema evolution. Mitigation: every `PolicyStateSnapshot` has a `version: 1` field; `migrate()` upgrades older snapshots; deserialization failure logs and resets to defaults rather than crashing.

**R5 — Phase 6 test rewrite**: removing `confidence` requires updating FSM tests in-place. Mitigation: do not delete existing tests — adapt assertions from `confidence=5` to `scores=Scores(...)` to prove same FSM behaviors. Budget time explicitly for this in Phase 6.

**R6 — Activity loading in Phase 3**: needs `teacher_goal` in addition to `context_description`. Mitigation: `ActivityRef` is optional on `PolicyContext`, defaults to `None`; when `None`, contextualizer is a no-op. Keeps existing tests passing.

**R7 — Bottom-out tension with `NoDirectAnswersRule`**: hint ladder's `BOTTOM_OUT` directive ("give worked sub-step") conflicts with the rule. Mitigation: when `hint_state == BOTTOM_OUT`, set `plan.constraints.forbid_direct_answer = False` and add directive: "Give the next sub-step only, not the final answer; end with a check question." Direct-answer interceptor reads `forbid_direct_answer` and skips its check when False; rhetorical interceptor still runs.

**R8 — Recovery state could starve FSM progression**: pathologically confused session never advances out of PLANNING. Mitigation: cap recovery duration at 4 turns; on exit, force advance one FSM step regardless of turn-count threshold.

**R9 — Phase 4 scope creep**: Phase 4 is the largest by line count. Mitigation: if pressure rises, split into 4a (hint ladder + cooldown) and 4b (recovery). Each independently shippable.

---

## 7. Critical Files Summary

### Existing files modified across phases

| File | Phases that touch it | Key changes |
|---|---|---|
| [src/policy/engine.py](milo-back-agent-orchestrator/src/policy/engine.py) | 1, 2, 3, 4 | Score computation; rule order; cooldown filter; new interceptors |
| [src/policy/types.py](milo-back-agent-orchestrator/src/policy/types.py) | 1, 2, 3, 4, 6 | Extend signals; add scores; add ActivityRef; add hint/recovery state; remove confidence |
| [src/policy/fsm.py](milo-back-agent-orchestrator/src/policy/fsm.py) | 6 | Replace confidence reads with score-driven transitions |
| [src/policy/question_bank.py](milo-back-agent-orchestrator/src/policy/question_bank.py) | 3 | Becomes thin shim over new `questions/` module |
| [src/policy/rules/tone_by_confidence.py](milo-back-agent-orchestrator/src/policy/rules/tone_by_confidence.py) | 1 | Drive from scores not raw confidence |
| [src/policy/interceptors/direct_answer_detector.py](milo-back-agent-orchestrator/src/policy/interceptors/direct_answer_detector.py) | 2 | Use open-endedness scorer instead of bare `?` |
| [src/api/session.py](milo-back-agent-orchestrator/src/api/session.py) | 1, 3, 4, 5 | Build signals; load full activity; track new state; persist |
| [src/core/models.py](milo-back-agent-orchestrator/src/core/models.py) | 5 | Add `policy_state` JSON column to `ChatSession` |
| [src/services/metrics_evaluator.py](milo-back-agent-orchestrator/src/services/metrics_evaluator.py) | 5 | Capture `MetricsCollector.snapshot()` per session |
| [tests/policy/](milo-back-agent-orchestrator/tests/policy/) | all | New test files per phase; rewrite FSM tests in Phase 6 |

### New files created

**Phase 1**: `signals/__init__.py`, `signals/extractors.py`, `signals/aggregator.py`, `scores.py`
**Phase 2**: `interceptors/rhetorical_question_detector.py`, `interceptors/open_endedness_classifier.py`, `rules/elicit_attempt.py`
**Phase 3**: `questions/__init__.py`, `questions/families.py`, `questions/bank.py`, `questions/selector.py`, `questions/contextualizer.py`
**Phase 4**: `hint_ladder.py`, `recovery.py`, `cooldown.py`, `rules/hint_ladder_rule.py`
**Phase 5**: `persistence.py`, `evidence.py`, `metrics.py`
**Phase 6**: `tests/policy/test_acceptance.py` (no new src files)

### Reference utilities to reuse

- `PolicyEngine.evaluate / check_output` two-phase pattern ([src/policy/engine.py:40, 69](milo-back-agent-orchestrator/src/policy/engine.py#L40)) — extend, do not replace.
- `select_question` round-robin pattern ([src/policy/question_bank.py:34](milo-back-agent-orchestrator/src/policy/question_bank.py#L34)) — reuse for surface variant rotation in Phase 3.
- Existing `BaseRule` and `BaseOutputInterceptor` interfaces ([src/policy/rules/base.py](milo-back-agent-orchestrator/src/policy/rules/base.py), [src/policy/interceptors/base.py](milo-back-agent-orchestrator/src/policy/interceptors/base.py)) — all new components implement these directly.
- `ChatSession._process_turn` 6-step structure ([src/api/session.py:198-262](milo-back-agent-orchestrator/src/api/session.py#L198-L262)) — extend with persistence in Phase 5; do not refactor structure.
