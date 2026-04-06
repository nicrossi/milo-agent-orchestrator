from src.policy.question_bank import QUESTION_BANK, select_question
from src.policy.types import FSMState


def test_selects_unasked_question():
    qid, _ = select_question(FSMState.PLANNING, [])
    assert qid == "plan_01"


def test_skips_asked_questions():
    qid, _ = select_question(FSMState.PLANNING, ["plan_01"])
    assert qid == "plan_02"


def test_skips_multiple_asked_questions():
    qid, _ = select_question(FSMState.PLANNING, ["plan_01", "plan_02", "plan_03"])
    assert qid == "plan_04"


def test_fallback_when_all_asked():
    all_ids = [q[0] for q in QUESTION_BANK[FSMState.PLANNING]]
    qid, _ = select_question(FSMState.PLANNING, all_ids)
    assert qid == "plan_01"


def test_returns_tuple_with_text():
    qid, qtext = select_question(FSMState.PLANNING, [])
    assert isinstance(qid, str)
    assert isinstance(qtext, str)
    assert len(qtext) > 0


def test_monitoring_questions_exist():
    qid, _ = select_question(FSMState.MONITORING, [])
    assert qid == "mon_01"


def test_evaluation_questions_exist():
    qid, _ = select_question(FSMState.EVALUATION, [])
    assert qid == "eval_01"


def test_question_bank_counts():
    assert len(QUESTION_BANK[FSMState.PLANNING]) == 6
    assert len(QUESTION_BANK[FSMState.MONITORING]) == 6
    assert len(QUESTION_BANK[FSMState.EVALUATION]) == 5
