"""Tests for src/policy/metrics.py — MetricsCollector counters + snapshot."""
from src.policy.metrics import MetricsCollector
from src.policy.types import (
    FSMState,
    HintLadderState,
    PolicyDecision,
    QuestionPlan,
    RecoveryState,
    Scores,
)


def _decision(
    rules: list[str] | None = None,
    hint: HintLadderState = HintLadderState.PROCESS_FEEDBACK,
    recovery: RecoveryState = RecoveryState.NORMAL,
    miscalibration: float = 0.0,
) -> PolicyDecision:
    return PolicyDecision(
        next_state=FSMState.PLANNING,
        plan=QuestionPlan(question_id="q", question_text="?"),
        applied_rules=rules or [],
        scores=Scores(miscalibration=miscalibration),
        next_hint_state=hint,
        next_recovery_state=recovery,
    )


def test_initial_snapshot_is_zeroed():
    snap = MetricsCollector().snapshot()
    assert snap["total_turns"] == 0
    assert snap["direct_answer_leakage_rate"] == 0.0
    assert snap["over_intervention_rate"] == 0.0
    assert snap["recovery_entries"] == 0


def test_records_turn_count():
    m = MetricsCollector()
    for _ in range(5):
        m.record_decision(_decision())
    assert m.snapshot()["total_turns"] == 5


def test_hint_distribution_accumulates():
    m = MetricsCollector()
    m.record_decision(_decision(hint=HintLadderState.PROCESS_FEEDBACK))
    m.record_decision(_decision(hint=HintLadderState.STRATEGIC_HINT))
    m.record_decision(_decision(hint=HintLadderState.STRATEGIC_HINT))
    snap = m.snapshot()
    assert snap["hint_distribution"]["PROCESS_FEEDBACK"] == 1
    assert snap["hint_distribution"]["STRATEGIC_HINT"] == 2
    assert snap["hint_distribution"]["BOTTOM_OUT"] == 0


def test_over_intervention_rate():
    m = MetricsCollector()
    m.record_decision(_decision(rules=[]))               # 0 rules
    m.record_decision(_decision(rules=["a"]))            # 1 rule
    m.record_decision(_decision(rules=["a", "b"]))       # 2 rules → over-intervention
    m.record_decision(_decision(rules=["a", "b", "c"]))  # over
    snap = m.snapshot()
    # 2 of 4 turns had >1 rule → 0.5
    assert snap["over_intervention_rate"] == 0.5


def test_rule_firing_counts():
    m = MetricsCollector()
    m.record_decision(_decision(rules=["no_direct_answers", "tone_by_confidence"]))
    m.record_decision(_decision(rules=["no_direct_answers"]))
    snap = m.snapshot()
    assert snap["rule_firing_counts"]["no_direct_answers"] == 2
    assert snap["rule_firing_counts"]["tone_by_confidence"] == 1


def test_calibration_gap_proxy():
    m = MetricsCollector()
    m.record_decision(_decision(miscalibration=0.7))  # high
    m.record_decision(_decision(miscalibration=0.4))  # low
    m.record_decision(_decision(miscalibration=0.6))  # high
    snap = m.snapshot()
    # 2 of 3 turns had miscalibration > 0.5 → 0.667
    assert 0.6 < snap["calibration_gap_proxy"] < 0.7


def test_recovery_entries_count_only_normal_to_stabilize():
    m = MetricsCollector()
    m.record_decision(_decision(recovery=RecoveryState.NORMAL))
    m.record_decision(_decision(recovery=RecoveryState.STABILIZE))    # ENTER
    m.record_decision(_decision(recovery=RecoveryState.STABILIZE))    # stay
    m.record_decision(_decision(recovery=RecoveryState.NORMAL))       # exit
    m.record_decision(_decision(recovery=RecoveryState.STABILIZE))    # ENTER
    snap = m.snapshot()
    assert snap["recovery_entries"] == 2


def test_direct_answer_leakage_rate():
    m = MetricsCollector()
    for _ in range(10):
        m.record_decision(_decision())
        m.record_interceptor_correction(was_modified=False)
    # 1 leakage out of 10 turns
    m.record_interceptor_correction(was_modified=True)
    # The 11th correction without a record_decision; the rate is per turn so
    # we compute against last total_turns=10 → 0.1
    snap = m.snapshot()
    assert snap["direct_answer_leakage_rate"] == 0.1
