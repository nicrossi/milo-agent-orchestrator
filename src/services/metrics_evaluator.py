"""
MetricsEvaluator

Reads a completed chat session, sends the full transcript to the LLM
with the metrics rubric, and writes the structured result to session_metrics.
"""

import json
import logging
import uuid
import asyncio
from typing import Optional
from pathlib import Path

from datetime import datetime, timezone

from sqlalchemy import func, select

from src.core.database import get_db_session
from src.core.models import (
    ActivityCourseAssignment,
    ChatSession,
    Course,
    CourseEnrollment,
    ReflectionActivity,
    SessionMetric,
    SessionStatus,
    User,
)
from src.orchestration.agent import OrchestratorAgent
from src.services.email import frontend_base_url, render_button_email, send_email

logger = logging.getLogger("milo-orchestrator.metrics_evaluator")

_PROMPT_GUIDE_PATH = Path(__file__).parent.parent / "metrics" / "metrics_prompt_guide.md"
_OUTPUT_SCHEMA_PATH = Path(__file__).parent.parent / "metrics" / "metrics_output_schema.json"
_EXAMPLES_PATH = Path(__file__).parent.parent / "metrics" / "metrics_examples.md"
_REFLECTIVE_FRAMEWORK_GUIDE_PATH = Path(__file__).parent.parent / "metrics" / "reflective_framework_guide.md"

_evaluation_queue: asyncio.Queue = asyncio.Queue()
_worker_task: Optional[asyncio.Task] = None

async def queue_evaluation(session_id: uuid.UUID, agent: OrchestratorAgent) -> None:
    await _evaluation_queue.put((session_id, agent))
    logger.info("Queued evaluation for session %s (Queue size: %d)", session_id, _evaluation_queue.qsize())

async def start_worker() -> None:
    global _worker_task
    if _worker_task is None:
        _worker_task = asyncio.create_task(_evaluation_worker())
        logger.info("Started background evaluation worker.")

async def stop_worker() -> None:
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
        logger.info("Stopped background evaluation worker.")

async def _evaluation_worker() -> None:
    while True:
        try:
            session_id, agent = await _evaluation_queue.get()
            logger.info("Dequeued evaluation for session %s (Queue size: %d)", session_id, _evaluation_queue.qsize())
            
            retries = [5, 10, 20]
            max_attempts = len(retries) + 1
            
            for attempt in range(1, max_attempts + 1):
                try:
                    await evaluate_session(session_id, agent)
                    break
                except Exception as e:
                    e_str = str(e)
                    if "400" in e_str or "500" in e_str:
                        logger.error("Critical error (400/500) for session %s, failing immediately: %s", session_id, e_str)
                        await _mark_session_failed(session_id)
                        break
                    
                    if attempt < max_attempts:
                        wait_time = retries[attempt - 1]
                        logger.warning("Evaluation failed for session %s (Attempt %d/%d), retrying in %ds... Error: %s", 
                                       session_id, attempt, max_attempts, wait_time, e_str)
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error("All retries failed for session %s", session_id)
                        await _mark_session_failed(session_id)

            _evaluation_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Unexpected error in evaluation worker: %s", e, exc_info=True)


async def _mark_session_failed(session_id: uuid.UUID) -> None:
    try:
        async with get_db_session() as db:
            session = await db.get(ChatSession, session_id)
            if session:
                session.status = SessionStatus.EVALUATION_FAILED
                await db.commit()
    except Exception:
        logger.error("Could not mark session %s as EVALUATION_FAILED in _mark_session_failed", session_id, exc_info=True)

def _build_evaluation_prompt(
    transcript: str,
    teacher_goal: str,
    context_description: str,
) -> str:
    prompt_guide = _PROMPT_GUIDE_PATH.read_text()
    output_schema = _OUTPUT_SCHEMA_PATH.read_text()
    examples = _EXAMPLES_PATH.read_text()
    reflective_framework_guide = _REFLECTIVE_FRAMEWORK_GUIDE_PATH.read_text()

    return f"""
    
[System Role]

You are an objective Expert Educational Analyst specializing in student metacognition. 
Your task is to analyze chat transcripts between an AI educational agent and a student, 
extracting standardized metrics regarding the student's reflective process.    
    

[Context & Definitions]
You must evaluate the student's dialogue based strictly on the following pedagogical frameworks. 
Do not rely on subjective feelings; look for concrete linguistic evidence.
{reflective_framework_guide}
{prompt_guide}

[Rules & Constraints]
- Rely STRICTLY on the provided transcript. Do not infer feelings or thoughts the student did not explicitly state.
- If the transcript is too short or lacks substantive interaction (e.g., just greetings), output `null` for all the metric fields.
- Output your response ONLY as valid, raw JSON. Do not wrap the JSON in markdown code blocks. Do not include any conversational filter.
- "evidence" must be a list of 1–3 direct quotes from the student's messages
- "level" must be assigned according to the metric's specific rubric.
- "justification" must be a single concise paragraph
- "recommended_action" must contain 1-2 concrete, actionable guidelines or points the teacher can use to guide the student to improve this specific metric.
- do not reward verbosity by itself
- short answers can still be good if they are precise and meaningful
- Language conversation should match user's input

[Input]
Student's goal (provided by the teacher, not shown to the student): {teacher_goal}
Activity description: {context_description}
Transcript: {transcript}

[Expected Output Format]
{output_schema}

""".strip()


def _parse_llm_response(raw: str) -> dict:
    """Extract and parse the JSON object from the LLM response."""
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


async def evaluate_session(session_id: uuid.UUID, agent: 'OrchestratorAgent') -> None:
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

            stmt = (
                select(ChatSession.transcript)
                .where(
                    ChatSession.activity_id == session.activity_id,
                    ChatSession.student_id == session.student_id,
                    ChatSession.transcript != ""
                )
                .order_by(ChatSession.started_at.asc())
            )
            result = await db.execute(stmt)
            transcripts = result.scalars().all()
            
            transcript = "\n\n".join(transcripts).strip()
            
            if not transcript:
                logger.info("Empty cumulative transcript for session %s. Skipping LLM evaluation.", session_id)
                session.status = SessionStatus.EVALUATED
                return

            prompt = _build_evaluation_prompt(
                transcript=transcript,
                teacher_goal=activity.teacher_goal,
                context_description=activity.context_description,
            )

        # Call LLM outside the DB session to avoid holding the connection
        raw_response = await agent.generate_evaluation(prompt)
        parsed = _parse_llm_response(raw_response)

        # Write metrics and update session status
        async with get_db_session() as db:
            metrics = parsed.get("metrics") or parsed

            _rq_default = {
                "level": "basic",
                "justification": "Not evaluated",
                "evidence": [],
                "recommended_action": "None"
            }
            _cal_default = {
                "level": "aligned",
                "justification": "Not evaluated",
                "evidence": [],
                "recommended_action": "None"
            }
            _ct_default = {
                "level": "meaningful",
                "justification": "Not evaluated",
                "evidence": [],
                "recommended_action": "None"
            }

            rq = metrics.get("reflection_quality") or _rq_default
            cal = metrics.get("calibration") or _cal_default
            ct = metrics.get("contextual_transfer") or _ct_default

            metric = await db.get(SessionMetric, session_id)
            if metric is None:
                metric = SessionMetric(session_id=session_id)
                db.add(metric)

            metric.reflection_quality_level = rq.get("level")
            metric.reflection_quality_justification = rq.get("justification")
            metric.reflection_quality_evidence = rq.get("evidence")
            metric.reflection_quality_action = rq.get("recommended_action")
            
            metric.calibration_level = cal.get("level")
            metric.calibration_justification = cal.get("justification")
            metric.calibration_evidence = cal.get("evidence")
            metric.calibration_action = cal.get("recommended_action")
            
            metric.contextual_transfer_level = ct.get("level")
            metric.contextual_transfer_justification = ct.get("justification")
            metric.contextual_transfer_evidence = ct.get("evidence")
            metric.contextual_transfer_action = ct.get("recommended_action")

            session = await db.get(ChatSession, session_id)
            session.status = SessionStatus.EVALUATED
            activity_id = session.activity_id
            await db.commit()

        logger.info("Evaluation complete for session %s", session_id)

        try:
            await _maybe_notify_teacher_all_completed(activity_id)
        except Exception:
            logger.exception(
                "Failed completion-notification check for activity %s",
                activity_id,
            )

    except Exception as e:
        logger.error("Evaluation failed for session %s: %s", session_id, e, exc_info=True)
        # We no longer mark EVALUATION_FAILED here; we let the worker handle it during retries
        raise


async def _maybe_notify_teacher_all_completed(activity_id) -> None:
    """If every student enrolled in any course assigned to this activity has at
    least one EVALUATED session, send the teacher a "everyone has finished"
    email. Idempotent: writes all_completed_notified_at so the email never
    fires twice for the same activity."""
    async with get_db_session() as db:
        activity = await db.get(ReflectionActivity, activity_id)
        if not activity or activity.all_completed_notified_at is not None:
            return

        course_ids = [
            row[0]
            for row in (
                await db.execute(
                    select(ActivityCourseAssignment.course_id).where(
                        ActivityCourseAssignment.activity_id == activity_id
                    )
                )
            ).all()
        ]
        if not course_ids:
            return  # Unscoped activity — no defined cohort to "complete".

        enrolled_count = (
            await db.execute(
                select(func.count(func.distinct(CourseEnrollment.student_id))).where(
                    CourseEnrollment.course_id.in_(course_ids)
                )
            )
        ).scalar_one()
        if not enrolled_count:
            return

        # Cohort completion is keyed off finalized_at, NOT status=EVALUATED.
        # The LLM is the sole judge of "finished"; the metrics-evaluation
        # lifecycle (PENDING_EVALUATION → EVALUATED) runs on every
        # disconnect even for half-done sessions, so it would falsely
        # trigger this notification.
        finalized_students_subq = (
            select(ChatSession.student_id)
            .where(ChatSession.activity_id == activity_id)
            .where(ChatSession.finalized_at.is_not(None))
            .distinct()
            .subquery()
        )
        finalized_in_cohort = (
            await db.execute(
                select(func.count(func.distinct(CourseEnrollment.student_id)))
                .where(CourseEnrollment.course_id.in_(course_ids))
                .where(CourseEnrollment.student_id.in_(select(finalized_students_subq)))
            )
        ).scalar_one()

        if finalized_in_cohort < enrolled_count:
            return

        teacher = await db.get(User, activity.created_by_id)
        if not teacher or not teacher.email:
            logger.warning(
                "Activity %s has no teacher email; cannot send completion notice.",
                activity_id,
            )
            return

        # Mark BEFORE sending so concurrent evaluations don't double-fire.
        activity.all_completed_notified_at = datetime.now(timezone.utc)
        await db.commit()

        teacher_email = teacher.email
        teacher_name = teacher.display_name or ""
        title = activity.title

    link = f"{frontend_base_url()}/?activity={activity_id}&view=analytics"
    greeting = f"Hi {teacher_name}," if teacher_name else "Hi,"
    body_html = (
        f"<p>{greeting}</p>"
        f"<p>Every student enrolled in <strong>{title}</strong> has now completed "
        f"and been evaluated for this activity.</p>"
        f"<p>The analytics dashboard has the full breakdown of metrics and per-student results.</p>"
    )
    html = render_button_email(
        headline="All students have completed",
        body_html=body_html,
        cta_label="View analytics",
        cta_url=link,
    )
    await send_email(
        to=teacher_email,
        subject=f"All students completed: {title}",
        html=html,
    )

