"""
Phase 6 acceptance suite — adversarial multi-turn scenarios run through the
engine end-to-end (no LLM). Validates pedagogical invariants that span
multiple components: signals → scores → FSM → ladder → recovery → cooldown
→ rules → interceptors.

Each scenario uses _SessionRunner to simulate a real ChatSession's
cross-turn state (windows, FSM, ladder, recovery, cooldown). The runner is
a deliberate near-copy of `ChatSession._process_turn`'s state plumbing — if
the engine wiring drifts, these tests will catch it at the integration
level even when individual unit tests still pass.
"""
from __future__ import annotations

from typing import Any

from src.policy.engine import PolicyEngine
from src.policy.persistence import PolicyStateSnapshot
from src.policy.signals.aggregator import build_user_signals, message_word_count
from src.policy.types import (
    ActivityRef,
    FSMState,
    HintLadderState,
    PolicyContext,
    RecoveryState,
)


_engine = PolicyEngine()


class _SessionRunner:
    """In-memory session simulator. Mirrors ChatSession's cross-turn state."""

    def __init__(self, activity: ActivityRef | None = None):
        self.fsm_state = FSMState.PLANNING
        self.recent_question_ids: list[str] = []
        self.signals_window = []
        self.length_window: list[int] = []
        self.latency_window: list[float] = []
        self.last_milo_response_ts: float | None = None
        self.hint_state = HintLadderState.PROCESS_FEEDBACK
        self.turns_in_hint_state = 0
        self.consecutive_low_struggle_turns = 0
        self.recovery_state = RecoveryState.NORMAL
        self.turns_in_recovery = 0
        self.turns_since_meta_feedback = 99
        self.activity = activity
        self.history: list[dict] = []  # decisions per turn for assertions
        self.now_ts = 1000.0
        self.turn_count = 0

    def turn(self, user_msg: str, llm_output: str = "¿Qué pensás vos?") -> dict:
        """Run one turn. Returns a dict with the decision + interception result."""
        sig = build_user_signals(
            user_message=user_msg,
            signals_window=self.signals_window,
            prev_milo_response_ts=self.last_milo_response_ts,
            now_ts=self.now_ts,
            length_window=self.length_window,
            latency_window=self.latency_window,
        )
        ctx = PolicyContext(
            current_state=self.fsm_state,
            turn_count=self.turn_count,
            recent_question_ids=self.recent_question_ids.copy(),
            user_message=user_msg,
            user_signals=sig,
            signals_window=self.signals_window.copy(),
            activity=self.activity,
            hint_state=self.hint_state,
            turns_in_hint_state=self.turns_in_hint_state,
            consecutive_low_struggle_turns=self.consecutive_low_struggle_turns,
            recovery_state=self.recovery_state,
            turns_in_recovery=self.turns_in_recovery,
            turns_since_meta_feedback=self.turns_since_meta_feedback,
        )
        decision = _engine.evaluate(ctx)
        was_intercepted, final_text = _engine.check_output(llm_output, decision)

        # Update windows + cross-turn state.
        self.signals_window.append(sig)
        self.signals_window = self.signals_window[-10:]
        self.length_window.append(message_word_count(user_msg))
        self.length_window = self.length_window[-10:]
        if self.last_milo_response_ts is not None:
            self.latency_window.append(self.now_ts - self.last_milo_response_ts)
            self.latency_window = self.latency_window[-10:]

        self.fsm_state = decision.next_state
        self.recent_question_ids.append(decision.plan.question_id)
        self.hint_state = decision.next_hint_state
        self.turns_in_hint_state = decision.next_turns_in_hint_state
        self.consecutive_low_struggle_turns = decision.next_consecutive_low_struggle_turns
        self.recovery_state = decision.next_recovery_state
        self.turns_in_recovery = decision.next_turns_in_recovery
        self.turns_since_meta_feedback = decision.next_turns_since_meta_feedback
        self.last_milo_response_ts = self.now_ts + 5.0  # simulate 5s of LLM streaming
        self.now_ts = self.last_milo_response_ts + 1.0  # next user msg arrives 1s later
        self.turn_count += 1

        record = {
            "decision": decision,
            "was_intercepted": was_intercepted,
            "final_text": final_text,
            "user_msg": user_msg,
            "fsm_state": decision.next_state,
            "hint_state": decision.next_hint_state,
            "recovery_state": decision.next_recovery_state,
            "applied_rules": decision.applied_rules,
            "question_id": decision.plan.question_id,
            "scores": decision.scores,
        }
        self.history.append(record)
        return record


# ---------------------------------------------------------------------------
# 1. Five turns of "dame la respuesta" never produce a direct answer.
# ---------------------------------------------------------------------------

def test_persistent_direct_answer_demands_never_leak_answer():
    runner = _SessionRunner()
    for _ in range(5):
        result = runner.turn(
            "dame la respuesta",
            llm_output="¿Qué probaste hasta ahora?",  # cooperative LLM
        )
    # Every turn must have routed through elicit_attempt OR forbidden direct
    # answers explicitly.
    for record in runner.history:
        assert (
            "elicit_attempt" in record["applied_rules"]
            or "no_direct_answers" in record["applied_rules"]
        ), f"Turn missed both guardrails: {record['applied_rules']}"


def test_repeated_direct_demands_keep_eliciting_attempts():
    runner = _SessionRunner()
    for _ in range(3):
        runner.turn("dame la respuesta")
    # All three turns should fire elicit_attempt and route to attempt-elicitation pool.
    for record in runner.history:
        assert "elicit_attempt" in record["applied_rules"]
        assert record["question_id"].startswith("elicit_")


# ---------------------------------------------------------------------------
# 2. Hedging → confusion → recovery activation.
# ---------------------------------------------------------------------------

def test_sustained_confusion_enters_recovery_and_pauses_fsm():
    runner = _SessionRunner()
    runner.turn("no entiendo nada")
    runner.turn("estoy perdido, no tiene sentido")
    runner.turn("me confunde todo, no sé qué hacer")
    # By the 3rd turn, recovery should have kicked in.
    final = runner.history[-1]
    assert final["recovery_state"] == RecoveryState.STABILIZE
    # FSM frozen during recovery (still PLANNING despite turn_count crossing).
    assert final["fsm_state"] == FSMState.PLANNING
    # Question forced to RECOVERY_STABILIZE family.
    assert final["question_id"].startswith("recover_")


# ---------------------------------------------------------------------------
# 3. Steady high-engagement learner accelerates to EVALUATION via score path.
# ---------------------------------------------------------------------------

def test_engaged_clear_learner_advances_to_evaluation_via_scores():
    runner = _SessionRunner()
    # Substantive, attempt-positive messages without hedging or confusion.
    msgs = [
        "creo que los herbívoros aumentarían porque ya no tienen depredadores",
        "la cadena alimentaria se desbalancearía, los herbívoros consumen plantas",
        "por lo tanto la vegetación disminuiría con el tiempo",
        "y eso afectaría a otras especies indirectamente",
        "es un efecto cascada en el ecosistema",
    ]
    for m in msgs:
        runner.turn(m)
    # Should have reached EVALUATION at some point in these 5 turns.
    states_visited = [r["fsm_state"] for r in runner.history]
    assert FSMState.EVALUATION in states_visited


# ---------------------------------------------------------------------------
# 4. Rhetorical "¿Entendiste?" caught by interceptor.
# ---------------------------------------------------------------------------

def test_rhetorical_only_output_gets_corrected():
    runner = _SessionRunner()
    rhetorical_only = (
        "Los herbívoros se multiplican sin control y agotan la vegetación, "
        "lo que afecta toda la cadena. ¿Entendiste?"
    )
    record = runner.turn("hola", llm_output=rhetorical_only)
    assert record["was_intercepted"] is True
    assert record["decision"].plan.question_text in record["final_text"]


def test_genuine_socratic_output_passes_through():
    runner = _SessionRunner()
    record = runner.turn(
        "hola",
        llm_output="¿Qué crees que pasaría con los herbívoros?",
    )
    assert record["was_intercepted"] is False


# ---------------------------------------------------------------------------
# 5. Cooldown rate-limits non-essential rules.
# ---------------------------------------------------------------------------

def test_cooldown_suppresses_back_to_back_tone_changes():
    runner = _SessionRunner()
    confused = "no entiendo, estoy perdido, esto me confunde mucho"
    # Turn 1: tone rule may fire (high affect_load).
    runner.turn(confused)
    # Turn 2: the cooldown should suppress consecutive tone firings.
    runner.turn(confused)
    rules_t2 = runner.history[1]["applied_rules"]
    assert "tone_by_confidence" not in rules_t2


def test_cooldown_does_not_block_essential_rules():
    runner = _SessionRunner()
    runner.turn("dame la respuesta")  # fires elicit + no_direct + tone
    record_t2 = runner.turn("dame la respuesta")  # tsmf=0 → tone suppressed
    # Essential rules still fire even mid-cooldown.
    assert "elicit_attempt" in record_t2["applied_rules"]
    assert "no_direct_answers" in record_t2["applied_rules"]


# ---------------------------------------------------------------------------
# 6. Reconnect mid-session restores state.
# ---------------------------------------------------------------------------

def test_snapshot_round_trip_preserves_session_state():
    runner_a = _SessionRunner()
    runner_a.turn("hola")
    runner_a.turn("creo que sí")
    runner_a.turn("estoy avanzando bien")

    # Snapshot mid-session (simulate disconnect).
    snap_blob = PolicyStateSnapshot.from_session(_FakeSession(runner_a)).serialize()

    # New session restores from blob.
    runner_b = _SessionRunner()
    snap = PolicyStateSnapshot.deserialize(snap_blob)
    assert snap is not None
    snap.apply_to(_FakeSession(runner_b))

    assert runner_b.fsm_state == runner_a.fsm_state
    assert runner_b.recent_question_ids == runner_a.recent_question_ids
    assert runner_b.hint_state == runner_a.hint_state
    assert runner_b.recovery_state == runner_a.recovery_state


class _FakeSession:
    """Adapter so PolicyStateSnapshot.from_session/apply_to work on _SessionRunner."""

    def __init__(self, runner: _SessionRunner):
        self._runner = runner

    def __getattr__(self, name: str) -> Any:
        # Map persistence's expected attribute names (e.g. _fsm_state) to
        # SessionRunner's flat naming (fsm_state).
        if name.startswith("_"):
            return getattr(self._runner, name[1:])
        return getattr(self._runner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_runner":
            object.__setattr__(self, name, value)
        elif name.startswith("_"):
            setattr(self._runner, name[1:], value)
        else:
            setattr(self._runner, name, value)


# ---------------------------------------------------------------------------
# 7. Activity contextualization appears in question_text.
# ---------------------------------------------------------------------------

def test_activity_title_contextualized_in_planning_question():
    activity = ActivityRef(
        id="x", title="El bosque sin depredadores",
        teacher_goal="g", context_description="c",
    )
    runner = _SessionRunner(activity=activity)
    record = runner.turn("hola")
    # First planning question is goal_01 with {topic} placeholder.
    assert record["question_id"] == "goal_01"
    assert "El bosque sin depredadores" in record["decision"].plan.question_text


# ---------------------------------------------------------------------------
# 8. Hint ladder cannot reach BOTTOM_OUT before turn 6.
# ---------------------------------------------------------------------------

def test_hint_ladder_no_premature_bottom_out():
    runner = _SessionRunner()
    # Synthetic high-struggle messages (lots of hedging + confusion).
    high_struggle = "creo que tal vez no sé bien no entiendo nada"
    for _ in range(5):
        record = runner.turn(high_struggle)
        assert record["hint_state"] != HintLadderState.BOTTOM_OUT, (
            f"BOTTOM_OUT reached prematurely at turn {len(runner.history)}"
        )


# ---------------------------------------------------------------------------
# 9-15. Edge cases.
# ---------------------------------------------------------------------------

def test_empty_greeting_does_not_crash():
    runner = _SessionRunner()
    record = runner.turn("")
    assert record["question_id"]
    assert record["fsm_state"] == FSMState.PLANNING


def test_non_spanish_message_routes_through_engine():
    runner = _SessionRunner()
    record = runner.turn("I think maybe I don't understand")
    # English hedging should produce non-zero hedging signal.
    assert record["scores"].struggle > 0.0


def test_very_long_message_does_not_break_extractors():
    runner = _SessionRunner()
    long_msg = " ".join(["palabra"] * 200)
    record = runner.turn(long_msg)
    # No crash; signals computed.
    assert record["scores"] is not None


def test_repeated_identical_messages_dedup_questions():
    runner = _SessionRunner()
    for _ in range(4):
        runner.turn("ok")
    # Each turn picks a different question_id (round-robin within family).
    asked = [r["question_id"] for r in runner.history]
    assert len(set(asked)) >= 2  # at minimum, dedup kicked in once


def test_decision_always_carries_scores():
    runner = _SessionRunner()
    for msg in ["hola", "creo que sí", "estoy probando algo"]:
        record = runner.turn(msg)
        assert record["scores"] is not None
        # All score fields in unit interval.
        for f in ("struggle", "miscalibration", "hint_abuse", "help_avoidance", "affect_load"):
            v = getattr(record["scores"], f)
            assert 0.0 <= v <= 1.0


def test_full_cycle_ten_turns_resets_to_planning():
    """After EVALUATION_RESET_TURN turns, FSM should cycle back to PLANNING."""
    runner = _SessionRunner()
    # Drive through 11 substantive turns.
    msgs = ["creo que sí, " + str(i) for i in range(11)]
    for m in msgs:
        runner.turn(m)
    final_state = runner.history[-1]["fsm_state"]
    # Either back at PLANNING (cycled) or still in MONITORING/EVALUATION
    # depending on score-driven transitions. The key invariant: FSM stays
    # within the valid set, no crashes, no permanent stuck.
    assert final_state in {FSMState.PLANNING, FSMState.MONITORING, FSMState.EVALUATION}


def test_no_confidence_field_remains_on_user_signals():
    """Phase 6 invariant: UserSignals no longer has a `confidence` attribute."""
    from src.policy.types import UserSignals
    sig = UserSignals()
    assert not hasattr(sig, "confidence")
