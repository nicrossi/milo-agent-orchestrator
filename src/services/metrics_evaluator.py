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

from sqlalchemy import select

from src.core.database import get_db_session
from src.core.models import (
    ChatSession,
    ReflectionActivity,
    SessionMetric,
    SessionStatus,
)
from src.orchestration.agent import OrchestratorAgent

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
) -> tuple[str, str]:
    """Return (static_prefix, dynamic_suffix).

    static_prefix holds rubric, framework, few-shot examples, schema and rules —
    stable across sessions, safe for provider-side context caching.
    dynamic_suffix holds per-session input (transcript, teacher goal, activity).
    """
    prompt_guide = _PROMPT_GUIDE_PATH.read_text()
    output_schema = _OUTPUT_SCHEMA_PATH.read_text()
    examples = _EXAMPLES_PATH.read_text()
    reflective_framework_guide = _REFLECTIVE_FRAMEWORK_GUIDE_PATH.read_text()

    static_prefix = f"""
[System Role]

You are an objective Expert Educational Analyst specializing in student metacognition.
Your task is to analyze chat transcripts between an AI educational agent and a student,
extracting standardized metrics regarding the student's reflective process.


[Context & Definitions]
You must evaluate the student's dialogue based strictly on the following pedagogical frameworks.
Do not rely on subjective feelings; look for concrete linguistic evidence.
{reflective_framework_guide}
{prompt_guide}

[Few-Shot Examples]
The following ground-truth examples illustrate how transcripts map onto the rubric for every
combination of reflection_quality / calibration / contextual_transfer. Use them as calibration
anchors when classifying a new transcript.
{examples}

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

[Expected Output Format]
{output_schema}
""".strip()

    dynamic_suffix = f"""
[Input]
Student's goal (provided by the teacher, not shown to the student): {teacher_goal}
Activity description: {context_description}
Transcript: {transcript}
""".strip()

    return static_prefix, dynamic_suffix


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

            static_prefix, dynamic_suffix = _build_evaluation_prompt(
                transcript=transcript,
                teacher_goal=activity.teacher_goal,
                context_description=activity.context_description,
            )

        # Call LLM outside the DB session to avoid holding the connection
        raw_response = await agent.generate_evaluation(static_prefix, dynamic_suffix)
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
            await db.commit()

        logger.info("Evaluation complete for session %s", session_id)

    except Exception as e:
        logger.error("Evaluation failed for session %s: %s", session_id, e, exc_info=True)
        # We no longer mark EVALUATION_FAILED here; we let the worker handle it during retries
        raise

