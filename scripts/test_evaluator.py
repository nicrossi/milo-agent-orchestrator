"""
Test script for the MetricsEvaluator.

Seeds the database with a realistic activity + session + conversation,
runs the evaluator, and prints the resulting metrics.

Usage:
    python scripts/test_evaluator.py
"""

import asyncio
import json
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
# Seed data — a realistic post-class reflection on fractions
# ---------------------------------------------------------------------------

TEACHER = {
    "id": "teacher-test-001",
    "email": "teacher@milo.edu",
    "display_name": "Ms. García",
}

STUDENT = {
    "id": "student-test-001",
    "email": "student@milo.edu",
    "display_name": "Lucas",
}

ACTIVITY = {
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

# A realistic conversation — student shows partial reflection, some calibration issues,
# and a decent but vague real-world connection.
CONVERSATION = [
    ("model", "Hi Lucas! Let's take a moment to think about today's class on fractions. What do you remember most about what we learned?"),
    ("user", "We learned about fractions like 1/2 and 1/4. They are parts of something."),
    ("model", "Good start! Can you explain in your own words what a fraction actually means?"),
    ("user", "It's when you divide something into equal parts and you take some of them. Like if you cut a pizza in 4 pieces and eat 2, that's 2/4."),
    ("model", "Nice example! Was there any part of the class that felt confusing or harder to understand?"),
    ("user", "Yeah, I got confused when we had fractions bigger than 1, like 5/4. I didn't understand how that was possible."),
    ("model", "That's a really honest observation. How do you feel about how much you understood the class overall?"),
    ("user", "I think I understood most of it, like maybe 80%. The basic fractions were easy but the ones bigger than 1 I still don't get."),
    ("model", "That's an interesting self-assessment. Can you think of a place in real life where you see fractions being used outside of school?"),
    ("user", "Maybe in cooking? Like recipes say half a cup of something. Or when you share things with friends."),
    ("model", "Good! Can you explain why fractions are useful in that cooking example specifically?"),
    ("user", "Because if you want to make less food you need to use less of each ingredient, so you need to know how to divide amounts."),
]


async def seed_db() -> tuple[uuid.UUID, uuid.UUID]:
    """Insert test data and return (activity_id, session_id)."""
    async with get_db_session() as db:
        # Upsert teacher
        teacher = await db.get(User, TEACHER["id"])
        if not teacher:
            db.add(User(**TEACHER))

        # Upsert student
        student = await db.get(User, STUDENT["id"])
        if not student:
            db.add(User(**STUDENT))

        await db.flush()

        # Create activity
        activity = ReflectionActivity(
            title=ACTIVITY["title"],
            teacher_goal=ACTIVITY["teacher_goal"],
            context_description=ACTIVITY["context_description"],
            created_by_id=TEACHER["id"],
        )
        db.add(activity)
        await db.flush()

        # Create session linked to activity
        session = ChatSession(
            activity_id=activity.id,
            student_id=STUDENT["id"],
            status=SessionStatus.PENDING_EVALUATION,
        )
        db.add(session)
        await db.flush()

        # Insert conversation messages
        for role, content in CONVERSATION:
            db.add(ChatMessage(
                session_id=session.id,
                user_id=STUDENT["id"],
                role=role,
                content=content,
            ))

        activity_id = activity.id
        session_id = session.id

    return activity_id, session_id


async def print_results(session_id: uuid.UUID) -> None:
    async with get_db_session() as db:
        metric = await db.get(SessionMetric, session_id)
        session = await db.get(ChatSession, session_id)

    if not metric:
        print("\n❌ No metrics found — evaluation may have failed.")
        return

    print(f"\n{'='*60}")
    print(f"Session status: {session.status}")
    print(f"{'='*60}\n")

    metrics = {
        "Reflection Quality": {
            "level": metric.reflection_quality_level,
            "justification": metric.reflection_quality_justification,
            "evidence": metric.reflection_quality_evidence,
            "recommended_action": metric.reflection_quality_action,
        },
        "Calibration": {
            "level": metric.calibration_level,
            "justification": metric.calibration_justification,
            "evidence": metric.calibration_evidence,
            "recommended_action": metric.calibration_action,
        },
        "Contextual Transfer": {
            "level": metric.contextual_transfer_level,
            "justification": metric.contextual_transfer_justification,
            "evidence": metric.contextual_transfer_evidence,
            "recommended_action": metric.contextual_transfer_action,
        },
    }

    for name, data in metrics.items():
        level = data["level"].upper() if data["level"] else "N/A"
        print(f"▸ {name}: {level}")
        print(f"  Justification: {data['justification']}")
        print(f"  Evidence:")
        for quote in (data["evidence"] or []):
            print(f"    - \"{quote}\"")
        print(f"  Recommended action: {data['recommended_action']}")
        print()


async def main():
    print("Initialising database...")
    await init_db()

    print("Seeding test data...")
    activity_id, session_id = await seed_db()
    print(f"  Activity ID : {activity_id}")
    print(f"  Session ID  : {session_id}")

    print("\nRunning evaluator...")
    await evaluate_session(session_id)

    await print_results(session_id)


if __name__ == "__main__":
    asyncio.run(main())
