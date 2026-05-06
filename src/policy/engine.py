"""
PolicyEngine — two-phase per-turn policy orchestrator.

Phase 1 (pre-LLM): evaluate(ctx) → PolicyDecision
  Runs the per-turn pipeline:
    1. compute scores from signals
    2. recovery state transition (may pause FSM / force question family)
    3. FSM transition (paused if in STABILIZE)
    4. hint ladder transition (frozen if in STABILIZE)
    5. question selection (forced to RECOVERY_STABILIZE family if recovering)
    6. rules with cooldown filtering for non-essential rules
  Returns a PolicyDecision with directives, scores, and the next-turn cross-
  state values for the caller (session) to persist.

Phase 2 (post-LLM): check_output(raw, decision) → (was_modified, final_text)
  Runs interceptors. The DirectAnswerDetector is skipped when
  decision.plan.constraints.forbid_direct_answer is False (which the
  HintLadderRule sets in BOTTOM_OUT — the rung is intentionally a near-answer).
"""
from src.policy.cooldown import MetaFeedbackCooldown
from src.policy import fsm as fsm_mod
from src.policy import hint_ladder as hl_mod
from src.policy import recovery as recovery_mod
from src.policy.fsm import PolicyFSM
from src.policy.hint_ladder import next_step as hint_next_step
from src.policy.interceptors.base import BaseOutputInterceptor
from src.policy.interceptors.direct_answer_detector import DirectAnswerDetectorInterceptor
from src.policy.interceptors.rhetorical_question_detector import (
    RhetoricalQuestionDetectorInterceptor,
)
from src.policy.questions.contextualizer import contextualize
from src.policy.questions.families import QuestionFamily
from src.policy.questions.selector import family_preference, select_question
from src.policy.recovery import next_state as recovery_next_state
from src.policy.rules.base import BaseRule
from src.policy.rules.elicit_attempt import ElicitAttemptRule
from src.policy.rules.hint_ladder_rule import HintLadderRule
from src.policy.rules.no_direct_answers import NoDirectAnswersRule
from src.policy.rules.procedural_unblock import ProceduralUnblockRule
from src.policy.rules.tone_by_confidence import ToneByConfidenceRule
from src.policy.scores import compute_scores
from src.policy.types import (
    FSMState,
    HintLadderState,
    PolicyContext,
    PolicyDecision,
    PolicyTrace,
    QuestionPlan,
    RecoveryState,
    ResponseConstraints,
    StageTrace,
)

# Token the LLM emits at the very end of its response when it judges the
# reflection has reached natural closure. session.py parses + strips it from
# the streamed output and finalizes the chat session in DB.
CLOSURE_SENTINEL = "[[END_REFLECTION]]"

# Earliest turn at which closure is allowed. Below this, even if the LLM emits
# the sentinel, session.py will ignore it (logged warning). Prevents accidental
# closures of conversations that barely started.
CLOSURE_MIN_TURNS = 4

_CLOSURE_DIRECTIVE = (
    "Closure protocol. ONLY when the student has clearly reached a natural "
    "endpoint of their reflection (they articulated a takeaway, named a "
    "concrete next step, or otherwise signaled they are done), produce a "
    "closing turn instead of another Socratic question. The closing turn "
    "must contain ALL of:\n"
    "  1. A short, warm acknowledgement of what they reflected on (1 sentence).\n"
    "  2. An explicit statement that the activity is now complete — phrasing "
    "like 'Your reflection on this activity is complete' or 'You can finish "
    "this activity here'. Match the language the student has been using.\n"
    "  3. The literal token "
    f"{CLOSURE_SENTINEL} on its OWN final line, with no other text after it.\n"
    "Do NOT ask any question in a closing turn. Do NOT emit the token in any "
    "other situation, do not mention it to the student, and do not emit it "
    "after a single short message. When in doubt, do not close: continue the "
    "Socratic dialogue as planned."
)

# Module-level singletons — stateless, safe to share across all sessions.
# Rule order matters: ElicitAttemptRule runs first so it can override the
# planned question before downstream rules attach directives. HintLadderRule
# always runs to attach the rung-appropriate scaffold directive.
_fsm = PolicyFSM()
_rules: list[BaseRule] = [
    ElicitAttemptRule(),       # essential — routes turn (first-attempt fishing)
    NoDirectAnswersRule(),     # essential — guardrail
    HintLadderRule(),          # essential — scaffold directive
    ToneByConfidenceRule(),    # non-essential — cosmetic, cooldown-filtered
    # Runs LAST so it can override the directive stack with the Scaffolding
    # Pivot directive when fired — earlier rules' directives would otherwise
    # contradict the prereq hand-off ("comment without giving any concrete
    # next step" vs. "hand off the fact"). When the rule does not fire, the
    # rest of the pipeline behaves as before.
    ProceduralUnblockRule(),   # essential — Scaffolding Pivot for prereq gaps
]
_interceptors: dict[str, BaseOutputInterceptor] = {
    "direct_answer_detector": DirectAnswerDetectorInterceptor(),
    "rhetorical_question_detector": RhetoricalQuestionDetectorInterceptor(),
}
# Default interceptor order: rhetorical FIRST (catches assertion+rhetorical
# patterns where direct_answer_detector would otherwise mis-classify the
# response as having an open question). If rhetorical fires, the corrected
# text already has an appended Socratic question, so direct_answer_detector
# becomes a no-op on the second pass.
_DEFAULT_INTERCEPTORS = ["rhetorical_question_detector", "direct_answer_detector"]


def _recovery_reason(prev, nxt, prev_turns, scores, signals, window) -> str:
    """Best-effort human-readable reason for the recovery transition."""
    high_affect = recovery_mod._high_affect_count(window)
    if prev == RecoveryState.STABILIZE and nxt == RecoveryState.NORMAL:
        if prev_turns + 1 >= recovery_mod._MAX_RECOVERY_TURNS:
            return f"exit: turns_in_recovery+1 ≥ {recovery_mod._MAX_RECOVERY_TURNS}"
        return (
            f"exit: affect_load={scores.affect_load:.2f} < {recovery_mod._AFFECT_LOW} "
            f"∧ confusion={signals.confusion:.2f} < {recovery_mod._CONFUSION_LOW}"
        )
    if prev == RecoveryState.STABILIZE and nxt == RecoveryState.STABILIZE:
        return "stay: still recovering"
    if prev == RecoveryState.NORMAL and nxt == RecoveryState.STABILIZE:
        return (
            f"enter: affect_load={scores.affect_load:.2f} > {recovery_mod._AFFECT_HIGH} "
            f"∧ confusion={signals.confusion:.2f} > {recovery_mod._CONFUSION_HIGH} "
            f"∧ high_affect_window={high_affect} ≥ {recovery_mod._MIN_AFFECT_TURNS_IN_WINDOW}"
        )
    # NORMAL → NORMAL — explain which entry gate failed
    if scores.affect_load <= recovery_mod._AFFECT_HIGH:
        return f"stay: affect_load={scores.affect_load:.2f} ≤ {recovery_mod._AFFECT_HIGH}"
    if signals.confusion <= recovery_mod._CONFUSION_HIGH:
        return f"stay: confusion={signals.confusion:.2f} ≤ {recovery_mod._CONFUSION_HIGH}"
    return (
        f"stay: high_affect_window={high_affect} < {recovery_mod._MIN_AFFECT_TURNS_IN_WINDOW}"
    )


def _fsm_reason(prev, nxt, ctx, paused: bool) -> str:
    if paused:
        return "paused: STABILIZE"
    turn = ctx.turn_count
    scores = ctx.scores
    if prev == FSMState.PLANNING and nxt == FSMState.MONITORING:
        return f"turn={turn} ≥ {fsm_mod.PLANNING_TO_MONITORING_TURN}"
    if prev == FSMState.PLANNING:
        return f"stay: turn={turn} < {fsm_mod.PLANNING_TO_MONITORING_TURN}"
    if prev == FSMState.MONITORING and nxt == FSMState.EVALUATION:
        if turn >= fsm_mod.MONITORING_TO_EVALUATION_TURN:
            return f"turn={turn} ≥ {fsm_mod.MONITORING_TO_EVALUATION_TURN}"
        return (
            f"accelerated: turn={turn} ≥ {fsm_mod.MIN_TURNS_BEFORE_ACCEL} ∧ attempt ∧ "
            f"miscalibration<{fsm_mod.CLEAR_THRESHOLD} ∧ struggle<{fsm_mod.CLEAR_THRESHOLD} "
            f"∧ affect_load<{fsm_mod.CLEAR_THRESHOLD}"
        )
    if prev == FSMState.MONITORING:
        if scores is None:
            return "stay: scores unavailable"
        return (
            f"stay: gates not met (turn={turn}, miscal={scores.miscalibration:.2f}, "
            f"strug={scores.struggle:.2f}, affect={scores.affect_load:.2f}, "
            f"attempt={ctx.user_signals.attempt_present})"
        )
    if prev == FSMState.EVALUATION and nxt == FSMState.PLANNING:
        if turn >= fsm_mod.EVALUATION_RESET_TURN:
            return f"reset: turn={turn} ≥ {fsm_mod.EVALUATION_RESET_TURN}"
        return (
            f"reset: overload (struggle={scores.struggle:.2f} or "
            f"affect_load={scores.affect_load:.2f} > {fsm_mod.OVERLOAD_THRESHOLD})"
        )
    if prev == FSMState.EVALUATION:
        return f"stay: turn={turn} < {fsm_mod.EVALUATION_RESET_TURN}, no overload"
    return f"{prev.value} → {nxt.value}"


def _hint_reason(prev, nxt, prev_turns, scores, signals, recovery) -> str:
    if recovery == RecoveryState.STABILIZE:
        return "frozen: STABILIZE"
    if scores.struggle < hl_mod._STRUGGLE_LOW:
        if prev != HintLadderState.PROCESS_FEEDBACK and nxt == HintLadderState.PROCESS_FEEDBACK:
            return f"reset: ≥{hl_mod._LOW_STRUGGLE_RESET} consecutive low_struggle turns"
        return f"low struggle ({scores.struggle:.2f} < {hl_mod._STRUGGLE_LOW}), no advance"
    if not (scores.struggle > hl_mod._STRUGGLE_HIGH and signals.attempt_present):
        return (
            f"no advance: struggle={scores.struggle:.2f} (need >{hl_mod._STRUGGLE_HIGH}) "
            f"∧ attempt={signals.attempt_present}"
        )
    if prev == HintLadderState.FOCUSED_HINT and nxt == HintLadderState.BOTTOM_OUT:
        return f"advance to BOTTOM_OUT: turns_at_focused={prev_turns} ≥ {hl_mod._MIN_TURNS_AT_FOCUSED}"
    if prev == HintLadderState.FOCUSED_HINT and nxt == HintLadderState.FOCUSED_HINT:
        return f"gate: turns_at_focused={prev_turns} < {hl_mod._MIN_TURNS_AT_FOCUSED}"
    if prev == HintLadderState.BOTTOM_OUT:
        return "saturated"
    return f"advance: struggle={scores.struggle:.2f} > {hl_mod._STRUGGLE_HIGH} ∧ attempt"


class PolicyEngine:
    """
    Stateless orchestrator — safe to share across sessions as a module-level singleton.
    All mutable state is in PolicyContext (caller-owned).
    """

    def evaluate(self, ctx: PolicyContext, collect_trace: bool = False) -> PolicyDecision:
        trace: PolicyTrace | None = None
        if collect_trace:
            trace = PolicyTrace(
                context_inputs={
                    "current_state": ctx.current_state.value,
                    "turn_count": ctx.turn_count,
                    "recent_question_ids": list(ctx.recent_question_ids),
                    "hint_state": ctx.hint_state.value,
                    "turns_in_hint_state": ctx.turns_in_hint_state,
                    "consecutive_low_struggle_turns": ctx.consecutive_low_struggle_turns,
                    "recovery_state": ctx.recovery_state.value,
                    "turns_in_recovery": ctx.turns_in_recovery,
                    "turns_since_meta_feedback": ctx.turns_since_meta_feedback,
                    "turns_since_procedural_unblock": ctx.turns_since_procedural_unblock,
                    "signals_window_size": len(ctx.signals_window),
                    "user_message": ctx.user_message,
                },
                user_signals=ctx.user_signals,
            )

        # 1. Compute scores
        ctx.scores = compute_scores(ctx.signals_window, ctx.user_signals)
        if trace is not None:
            trace.scores = ctx.scores
            trace.stages.append(StageTrace(
                name="scores",
                inputs={
                    "window_size": len(ctx.signals_window),
                    "current_signals": ctx.user_signals.model_dump(),
                },
                output=ctx.scores.model_dump(),
                transition_reason="aggregated from window + current signals",
            ))

        # 2. Recovery state transition (uses scores + window)
        prev_recovery = ctx.recovery_state
        next_recovery, next_turns_in_rec = recovery_next_state(
            current=ctx.recovery_state,
            turns_in_recovery=ctx.turns_in_recovery,
            scores=ctx.scores,
            user_signals=ctx.user_signals,
            signals_window=ctx.signals_window,
        )
        if trace is not None:
            trace.stages.append(StageTrace(
                name="recovery",
                inputs={
                    "prev_state": prev_recovery.value,
                    "prev_turns_in_recovery": ctx.turns_in_recovery,
                    "affect_load": ctx.scores.affect_load,
                    "confusion": ctx.user_signals.confusion,
                },
                output={
                    "next_state": next_recovery.value,
                    "next_turns_in_recovery": next_turns_in_rec,
                },
                transition_reason=_recovery_reason(
                    prev_recovery, next_recovery, ctx.turns_in_recovery,
                    ctx.scores, ctx.user_signals, ctx.signals_window,
                ),
            ))

        # 3. FSM transition — paused while in STABILIZE.
        prev_fsm = ctx.current_state
        if next_recovery == RecoveryState.STABILIZE:
            next_state = ctx.current_state
            fsm_paused = True
        else:
            next_state = _fsm.transition(ctx)
            fsm_paused = False
        if trace is not None:
            trace.stages.append(StageTrace(
                name="fsm",
                inputs={
                    "prev_state": prev_fsm.value,
                    "turn_count": ctx.turn_count,
                    "scores": ctx.scores.model_dump(),
                    "attempt_present": ctx.user_signals.attempt_present,
                },
                output={"next_state": next_state.value, "paused": fsm_paused},
                transition_reason=_fsm_reason(prev_fsm, next_state, ctx, fsm_paused),
            ))

        # 4. Hint ladder transition — frozen while in STABILIZE.
        prev_hint = ctx.hint_state
        prev_turns_in_hint = ctx.turns_in_hint_state
        next_hint, next_turns_in_hs, next_low = hint_next_step(
            current=ctx.hint_state,
            turns_in_state=ctx.turns_in_hint_state,
            consecutive_low_struggle=ctx.consecutive_low_struggle_turns,
            scores=ctx.scores,
            user_signals=ctx.user_signals,
            recovery_state=next_recovery,
        )
        if trace is not None:
            trace.stages.append(StageTrace(
                name="hint_ladder",
                inputs={
                    "prev_state": prev_hint.value,
                    "prev_turns_in_state": prev_turns_in_hint,
                    "consecutive_low_struggle": ctx.consecutive_low_struggle_turns,
                    "struggle": ctx.scores.struggle,
                    "attempt_present": ctx.user_signals.attempt_present,
                    "recovery_state": next_recovery.value,
                },
                output={
                    "next_state": next_hint.value,
                    "next_turns_in_state": next_turns_in_hs,
                    "next_consecutive_low_struggle": next_low,
                },
                transition_reason=_hint_reason(
                    prev_hint, next_hint, prev_turns_in_hint,
                    ctx.scores, ctx.user_signals, next_recovery,
                ),
            ))
        # Mutate ctx so the HintLadderRule sees this turn's ladder state.
        ctx.hint_state = next_hint

        # 5. Question selection — recovery forces RECOVERY_STABILIZE family.
        forced_family: QuestionFamily | None = None
        if next_recovery == RecoveryState.STABILIZE:
            forced_family = QuestionFamily.RECOVERY_STABILIZE
            question, variant = select_question(
                state=next_state,
                scores=ctx.scores,
                recent_ids=ctx.recent_question_ids,
                activity=ctx.activity,
                user_signals=ctx.user_signals,
                force_family=forced_family,
            )
        else:
            question, variant = select_question(
                state=next_state,
                scores=ctx.scores,
                recent_ids=ctx.recent_question_ids,
                activity=ctx.activity,
                user_signals=ctx.user_signals,
            )
        qtext = contextualize(variant, ctx.activity)

        if trace is not None:
            preferences = (
                [forced_family] if forced_family is not None
                else family_preference(next_state, ctx.scores, ctx.user_signals)
            )
            trace.stages.append(StageTrace(
                name="question_selection",
                inputs={
                    "fsm_state": next_state.value,
                    "forced_family": forced_family.value if forced_family else None,
                    "family_preferences": [f.value for f in preferences],
                    "recent_ids_excluded": list(ctx.recent_question_ids),
                },
                output={
                    "question_id": question.id,
                    "family": question.family.value,
                    "variant_text": qtext,
                    "tone": question.tone,
                },
                transition_reason=(
                    f"forced family: {forced_family.value}" if forced_family
                    else f"matched family preference: {question.family.value}"
                ),
            ))

        plan = QuestionPlan(
            question_id=question.id,
            question_text=qtext,
            tone=question.tone,
            constraints=ResponseConstraints(),
        )

        # 6. Rules with cooldown filtering on non-essential ones.
        cooldown = MetaFeedbackCooldown(ctx.turns_since_meta_feedback)
        applied: list[str] = []
        any_non_essential_fired = False
        rule_records: list[dict] = []
        for rule in _rules:
            rule_name = rule.__class__.__name__
            if not rule.essential and not cooldown.allows_intervention():
                if trace is not None:
                    rule_records.append({
                        "name": rule_name,
                        "essential": rule.essential,
                        "cooldown_blocked": True,
                        "fired": False,
                    })
                continue
            directives_before = len(plan.prompt_directives)
            result = rule.apply(ctx, plan)
            fired = result is not None
            if fired:
                applied.append(result)
                if not rule.essential:
                    any_non_essential_fired = True
            if trace is not None:
                rule_records.append({
                    "name": rule_name,
                    "essential": rule.essential,
                    "cooldown_blocked": False,
                    "fired": fired,
                    "directives_appended": len(plan.prompt_directives) - directives_before,
                })
        if trace is not None:
            trace.stages.append(StageTrace(
                name="rules",
                inputs={
                    "turns_since_meta_feedback": ctx.turns_since_meta_feedback,
                    "cooldown_allows_non_essential": cooldown.allows_intervention(),
                },
                output={
                    "applied": list(applied),
                    "rules": rule_records,
                },
                transition_reason=(
                    f"{len(applied)} rule(s) fired" if applied else "no rules fired"
                ),
            ))

        # Closure eligibility — let the LLM decide when reflection is done.
        # Inject the sentinel directive only after the student has had time to
        # actually reflect (>= CLOSURE_MIN_TURNS) and the FSM is past PLANNING.
        # Recovery (STABILIZE) and PLANNING explicitly exclude — wrapping up
        # a confused or barely-started reflection would be premature.
        closure_eligible = (
            ctx.turn_count >= CLOSURE_MIN_TURNS
            and next_state in (FSMState.MONITORING, FSMState.EVALUATION)
            and next_recovery == RecoveryState.NORMAL
        )
        if closure_eligible:
            plan.prompt_directives.append(_CLOSURE_DIRECTIVE)
        if trace is not None:
            reasons = []
            if ctx.turn_count < CLOSURE_MIN_TURNS:
                reasons.append(f"turn_count={ctx.turn_count} < {CLOSURE_MIN_TURNS}")
            if next_state not in (FSMState.MONITORING, FSMState.EVALUATION):
                reasons.append(f"fsm={next_state.value} not MONITORING/EVALUATION")
            if next_recovery != RecoveryState.NORMAL:
                reasons.append(f"recovery={next_recovery.value}")
            trace.closure_eligibility = {
                "eligible": closure_eligible,
                "reason": "all gates passed" if closure_eligible else "; ".join(reasons),
            }

        # Cooldown counter for ProceduralUnblockRule. Resets to 0 on the turn
        # the rule fires, otherwise increments (capped) so the rule can fire
        # again after a 2-turn cooldown.
        if "procedural_unblock" in applied:
            next_turns_since_proc_unblock = 0
        else:
            next_turns_since_proc_unblock = min(ctx.turns_since_procedural_unblock + 1, 999)

        return PolicyDecision(
            next_state=next_state,
            plan=plan,
            applied_rules=applied,
            interceptors=list(_DEFAULT_INTERCEPTORS),
            scores=ctx.scores,
            next_hint_state=next_hint,
            next_turns_in_hint_state=next_turns_in_hs,
            next_consecutive_low_struggle_turns=next_low,
            next_recovery_state=next_recovery,
            next_turns_in_recovery=next_turns_in_rec,
            next_turns_since_meta_feedback=cooldown.compute_next(any_non_essential_fired),
            next_turns_since_procedural_unblock=next_turns_since_proc_unblock,
            closure_eligible=closure_eligible,
            debug_trace=trace,
        )

    def check_output(self, raw: str, decision: PolicyDecision) -> tuple[bool, str]:
        """
        Run post-LLM interceptors on the accumulated response.

        Skips DirectAnswerDetector when the plan explicitly relaxes
        forbid_direct_answer — set by HintLadderRule in BOTTOM_OUT (worked
        sub-step) and by ProceduralUnblockRule (Scaffolding Pivot prereq
        hand-off). The rhetorical interceptor still runs.
        """
        text = raw
        was_modified = False
        for name in decision.interceptors:
            if name == "direct_answer_detector" and not decision.plan.constraints.forbid_direct_answer:
                continue
            if interceptor := _interceptors.get(name):
                modified, text = interceptor.process(text, decision.plan.question_text)
                if modified:
                    was_modified = True
        return was_modified, text
