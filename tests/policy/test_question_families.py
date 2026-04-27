"""Tests for the Phase 3 question bank — coverage, tagging invariants, no
duplicates, every state has minimum coverage."""
from src.policy.questions.bank import (
    Question,
    all_questions,
    by_family,
    by_id,
    by_state,
    by_state_and_family,
)
from src.policy.questions.families import QuestionFamily
from src.policy.types import FSMState


def test_bank_has_min_thirty_questions():
    assert len(all_questions()) >= 30


def test_unique_question_ids():
    ids = [q.id for q in all_questions()]
    assert len(ids) == len(set(ids))


def test_every_question_has_at_least_one_variant():
    for q in all_questions():
        assert len(q.surface_variants) >= 1
        assert all(v.strip() for v in q.surface_variants)


def test_every_question_tagged_with_known_family():
    valid_families = set(QuestionFamily)
    for q in all_questions():
        assert q.family in valid_families


def test_every_state_covered():
    for state in FSMState:
        assert len(by_state(state)) >= 1, f"No questions for {state.value}"


def test_each_canonical_state_has_minimum_questions():
    # Goal: enough variety per state for the round-robin to feel non-repetitive.
    assert len(by_state(FSMState.PLANNING)) >= 8
    assert len(by_state(FSMState.MONITORING)) >= 8
    assert len(by_state(FSMState.EVALUATION)) >= 6


def test_attempt_elicitation_family_populated():
    pool = by_family(QuestionFamily.ATTEMPT_ELICITATION)
    assert len(pool) >= 3
    for q in pool:
        # Attempt-elicitation should never require an attempt to be present.
        assert q.requires_attempt is False


def test_recovery_stabilize_family_present():
    # Phase 4 will use these; Phase 3 just declares them.
    pool = by_family(QuestionFamily.RECOVERY_STABILIZE)
    assert len(pool) >= 1


def test_by_id_finds_known_question():
    q = by_id("goal_01")
    assert q is not None
    assert q.family == QuestionFamily.GOAL_CLARIFICATION


def test_by_id_returns_none_on_unknown():
    assert by_id("does_not_exist") is None


def test_by_state_and_family_filter():
    questions = by_state_and_family(FSMState.PLANNING, QuestionFamily.ATTEMPT_ELICITATION)
    assert len(questions) >= 3
    for q in questions:
        assert q.state == FSMState.PLANNING
        assert q.family == QuestionFamily.ATTEMPT_ELICITATION


def test_difficulty_tags_within_range():
    for q in all_questions():
        assert 1 <= q.difficulty <= 3
        assert 0 <= q.escalation_level <= 3
