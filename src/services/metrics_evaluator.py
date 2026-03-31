"""
MetricsEvaluator

Reads a completed chat session, sends the full transcript to the LLM
with the metrics rubric, and writes the structured result to session_metrics.
"""

import json
import logging
import os
import uuid
from pathlib import Path

import google.genai as genai
from google.genai import types
from sqlalchemy import select

from src.core.database import get_db_session
from src.core.models import (
    ChatMessage,
    ChatSession,
    ReflectionActivity,
    SessionMetric,
    SessionStatus,
)

logger = logging.getLogger("milo-orchestrator.metrics_evaluator")

_PROMPT_GUIDE_PATH = Path(__file__).parent.parent / "metrics" / "metrics_prompt_guide.md"
_OUTPUT_SCHEMA_PATH = Path(__file__).parent.parent / "metrics" / "metrics_output_schema.json"
_EXAMPLES_PATH = Path(__file__).parent.parent / "metrics" / "metrics_examples.md"


def _build_evaluation_prompt(
    transcript: str,
    teacher_goal: str,
    context_description: str,
) -> str:
    prompt_guide = _PROMPT_GUIDE_PATH.read_text()
    output_schema = _OUTPUT_SCHEMA_PATH.read_text()
    examples = _EXAMPLES_PATH.read_text()

    return f"""
{prompt_guide}

---

## Annotated reference examples

Study these examples carefully before evaluating the new interaction.
They show the correct classification for a range of student responses.

{examples}

---

## Activity context (provided by the teacher, not shown to the student)

**Teacher goal:** {teacher_goal}

**Activity description:** {context_description}

---

## Student interaction transcript

{transcript}

---

## Your task

Evaluate the transcript above according to the metric-specific guidance.
Return ONLY a valid JSON object that strictly follows this schema:

{output_schema}

Rules:
- "level" must be one of: "red", "yellow", "green"
- "justification" must be a single concise paragraph
- "evidence" must be a list of 1–3 direct quotes from the student's messages
- "recommended_action" must be a single actionable sentence for the teacher
- Do not include any text outside the JSON object
""".strip()


def _format_transcript(messages: list[ChatMessage]) -> str:
    lines = []
    for msg in messages:
        speaker = "Student" if msg.role == "user" else "Milo"
        lines.append(f"{speaker}: {msg.content}")
    return "\n\n".join(lines)


def _parse_llm_response(raw: str) -> dict:
    """Extract and parse the JSON object from the LLM response."""
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


async def evaluate_session(session_id: uuid.UUID) -> None:
    """
    Run the metrics evaluation for a completed session.
    Updates session status to EVALUATED or EVALUATION_FAILED.
    """
    logger.info("Starting evaluation for session %s", session_id)

    try:
        async with get_db_session() as db:
            # Load session + activity
            session = await db.get(ChatSession, session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")

            activity = await db.get(ReflectionActivity, session.activity_id)
            if not activity:
                raise ValueError(f"Activity {session.activity_id} not found")

            # Load messages ordered chronologically
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
            )
            result = await db.execute(stmt)
            messages = result.scalars().all()

            if not messages:
                raise ValueError(f"No messages found for session {session_id}")

            transcript = _format_transcript(messages)
            prompt = _build_evaluation_prompt(
                transcript=transcript,
                teacher_goal=activity.teacher_goal,
                context_description=activity.context_description,
            )

        # Call LLM outside the DB session to avoid holding the connection
        raw_response = _call_llm(prompt)
        parsed = _parse_llm_response(raw_response)

        # Write metrics and update session status
        async with get_db_session() as db:
            rq = parsed["metrics"]["reflection_quality"]
            cal = parsed["metrics"]["calibration"]
            ct = parsed["metrics"]["contextual_transfer"]

            metric = SessionMetric(
                session_id=session_id,
                reflection_quality_level=rq["level"],
                reflection_quality_justification=rq["justification"],
                reflection_quality_evidence=rq["evidence"],
                reflection_quality_action=rq["recommended_action"],
                calibration_level=cal["level"],
                calibration_justification=cal["justification"],
                calibration_evidence=cal["evidence"],
                calibration_action=cal["recommended_action"],
                contextual_transfer_level=ct["level"],
                contextual_transfer_justification=ct["justification"],
                contextual_transfer_evidence=ct["evidence"],
                contextual_transfer_action=ct["recommended_action"],
            )
            db.add(metric)

            session = await db.get(ChatSession, session_id)
            session.status = SessionStatus.EVALUATED

        logger.info("Evaluation complete for session %s", session_id)

    except Exception as e:
        logger.error("Evaluation failed for session %s: %s", session_id, e, exc_info=True)
        try:
            async with get_db_session() as db:
                session = await db.get(ChatSession, session_id)
                if session:
                    session.status = SessionStatus.EVALUATION_FAILED
        except Exception:
            logger.error("Could not mark session %s as EVALUATION_FAILED", session_id, exc_info=True)
        raise


def _call_llm(prompt: str) -> str:
    """Synchronous LLM call for structured evaluation (no streaming needed)."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is required")

    client = genai.Client(api_key=api_key)
    model_name = os.getenv("LLM_MODEL", "gemini-2.5-flash")

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=4096,
        ),
    )
    return response.text
