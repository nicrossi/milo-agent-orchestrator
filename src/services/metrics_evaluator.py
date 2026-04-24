"""
MetricsEvaluator

Reads a completed chat session, sends the full transcript to the LLM
with the metrics rubric, and writes the structured result to session_metrics.
"""

import json
import logging
import uuid
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
        raw_response = agent.generate_evaluation(prompt)
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

            metric = SessionMetric(
                session_id=session_id,
                reflection_quality_level=rq.get("level"),
                reflection_quality_justification=rq.get("justification"),
                reflection_quality_evidence=rq.get("evidence"),
                reflection_quality_action=rq.get("recommended_action"),
                calibration_level=cal.get("level"),
                calibration_justification=cal.get("justification"),
                calibration_evidence=cal.get("evidence"),
                calibration_action=cal.get("recommended_action"),
                contextual_transfer_level=ct.get("level"),
                contextual_transfer_justification=ct.get("justification"),
                contextual_transfer_evidence=ct.get("evidence"),
                contextual_transfer_action=ct.get("recommended_action"),
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

