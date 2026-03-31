"""
Test script for the MetricsEvaluator.

Seeds the database with two realistic activities + sessions + conversations,
runs the evaluator on both, and prints the resulting metrics side by side.

Scenario A — Lucas: solid response (expected: green / green / green)
Scenario B — Martín: overconfident, vague (expected: red / red / red)

Usage:
    python scripts/test_evaluator.py
"""

import asyncio
import sys
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.core.database import init_db, get_db_session
from src.core.models import (
    ChatMessage,
    ChatSession,
    ReflectionActivity,
    SessionMetric,
    SessionStatus,
    User,
)
from src.services.metrics_evaluator import evaluate_session


# ---------------------------------------------------------------------------
# Shared users
# ---------------------------------------------------------------------------

TEACHER = {
    "id": "teacher-test-001",
    "email": "teacher@milo.edu",
    "display_name": "Ms. García",
}

STUDENT_A = {
    "id": "student-test-001",
    "email": "lucas@milo.edu",
    "display_name": "Lucas",
}

STUDENT_B = {
    "id": "student-test-002",
    "email": "martin@milo.edu",
    "display_name": "Martín",
}

ACTIVITY_META = {
    "title": "Reflection: Introduction to Fractions",
    "teacher_goal": (
        "Evaluate whether the student understood the concept of fractions as parts of a whole, "
        "can identify where they had difficulty, and can connect fractions to an everyday context."
    ),
    "context_description": (
        "Today we learned what fractions are: parts of a whole. "
        "We worked with halves, thirds, and quarters using diagrams and real-life examples like pizza and chocolate bars."
    ),
}

# ---------------------------------------------------------------------------
# Scenario A — Lucas: solid, specific, good calibration
# Expected: green / green / green
# ---------------------------------------------------------------------------

CONVERSATION_A = [
    ("model", "Hi Lucas! Let's take a moment to think about today's class on fractions. What do you remember most about what we learned?"),
    ("user", "We learned about fractions like 1/2 and 1/4. They are parts of something."),
    ("model", "Good start! Can you explain in your own words what a fraction actually means?"),
    ("user", "It's when you divide something into equal parts and you take some of them. Like if you cut a pizza in 4 pieces and eat 2, that's 2/4."),
    ("model", "Was there any part of the class that felt confusing or harder to understand?"),
    ("user", "Yeah, I got confused when we had fractions bigger than 1, like 5/4. I didn't understand how that was possible."),
    ("model", "How do you feel about how much you understood the class overall?"),
    ("user", "I think I understood most of it, like maybe 80%. The basic fractions were easy but the ones bigger than 1 I still don't get."),
    ("model", "Can you think of a place in real life where you see fractions being used outside of school?"),
    ("user", "Maybe in cooking? Like recipes say half a cup of something."),
    ("model", "Good! Can you explain why fractions are useful in that cooking example specifically?"),
    ("user", "Because if you want to make less food you need to use less of each ingredient, so you need to know how to divide amounts."),
]

# ---------------------------------------------------------------------------
# Scenario B — Martín: overconfident, vague, no real transfer
# Expected: red / red / red
# ---------------------------------------------------------------------------

CONVERSATION_B = [
    ("model", "Hi Martín! Let's reflect on today's class about fractions. What did you learn today?"),
    ("user", "We did fractions. I understood everything, it was pretty easy."),
    ("model", "Can you explain what a fraction is in your own words?"),
    ("user", "It's like a number with a line in the middle. The top and the bottom are numbers."),
    ("model", "Was there anything that was hard to understand?"),
    ("user", "No, not really. I got everything."),
    ("model", "How confident do you feel about the topic?"),
    ("user", "Very confident. I think I understood 100%."),
    ("model", "Can you think of a place where fractions come up in real life?"),
    ("user", "In math class I guess. Or maybe in science."),
    ("model", "Can you give me a more specific example from outside school?"),
    ("user", "I don't know… maybe when you do calculations?"),
]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def seed_users(db) -> None:
    for user_data in [TEACHER, STUDENT_A, STUDENT_B]:
        if not await db.get(User, user_data["id"]):
            db.add(User(**user_data))
    await db.flush()


async def seed_scenario(db, student_id: str, conversation: list) -> tuple[uuid.UUID, uuid.UUID]:
    activity = ReflectionActivity(
        title=ACTIVITY_META["title"],
        teacher_goal=ACTIVITY_META["teacher_goal"],
        context_description=ACTIVITY_META["context_description"],
        created_by_id=TEACHER["id"],
    )
    db.add(activity)
    await db.flush()

    session = ChatSession(
        activity_id=activity.id,
        student_id=student_id,
        status=SessionStatus.PENDING_EVALUATION,
    )
    db.add(session)
    await db.flush()

    for role, content in conversation:
        db.add(ChatMessage(
            session_id=session.id,
            user_id=student_id,
            role=role,
            content=content,
        ))

    return activity.id, session.id


async def print_results(label: str, session_id: uuid.UUID) -> None:
    async with get_db_session() as db:
        metric = await db.get(SessionMetric, session_id)
        session = await db.get(ChatSession, session_id)

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Session status: {session.status}")
    print(f"{'='*60}")

    if not metric:
        print("  ❌ No metrics found — evaluation may have failed.")
        return

    rows = [
        ("Reflection Quality", metric.reflection_quality_level, metric.reflection_quality_justification,
         metric.reflection_quality_evidence, metric.reflection_quality_action),
        ("Calibration",        metric.calibration_level,        metric.calibration_justification,
         metric.calibration_evidence,        metric.calibration_action),
        ("Contextual Transfer",metric.contextual_transfer_level,metric.contextual_transfer_justification,
         metric.contextual_transfer_evidence,metric.contextual_transfer_action),
    ]

    for name, level, justification, evidence, action in rows:
        print(f"\n  ▸ {name}: {(level or 'N/A').upper()}")
        print(f"    Justification: {justification}")
        print(f"    Evidence:")
        for quote in (evidence or []):
            print(f"      - \"{quote}\"")
        print(f"    Recommended action: {action}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("Initialising database...")
    await init_db()

    print("Seeding scenarios...")
    async with get_db_session() as db:
        await seed_users(db)
        _, session_a = await seed_scenario(db, STUDENT_A["id"], CONVERSATION_A)
        _, session_b = await seed_scenario(db, STUDENT_B["id"], CONVERSATION_B)

    print(f"  Scenario A (Lucas)  → session {session_a}")
    print(f"  Scenario B (Martín) → session {session_b}")

    print("\nRunning evaluator on Scenario A (Lucas)...")
    await evaluate_session(session_a)

    print("Running evaluator on Scenario B (Martín)...")
    await evaluate_session(session_b)

    await print_results("Scenario A — Lucas (expected: green / green / green)", session_a)
    await print_results("Scenario B — Martín (expected: red / red / red)", session_b)


if __name__ == "__main__":
    asyncio.run(main())
