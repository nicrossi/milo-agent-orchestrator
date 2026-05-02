"""
Background worker that emails students 30 minutes before an activity's deadline.

The worker wakes every SCAN_INTERVAL_SECONDS, finds activities whose deadline
falls inside the next REMINDER_WINDOW_MINUTES and which haven't had reminders
sent yet, then mails every enrolled student of any assigned course who has not
already had a session reach EVALUATED.

Idempotency: each activity carries `deadline_reminder_sent_at`; once set, it is
never picked up again, so duplicate sends across worker restarts are not
possible.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from src.core.database import get_db_session
from src.core.models import (
    ActivityCourseAssignment,
    ActivityStatus,
    ChatSession,
    CourseEnrollment,
    ReflectionActivity,
    User,
)
from src.services.email import frontend_base_url, render_button_email, send_email

logger = logging.getLogger("milo-orchestrator.deadline_reminders")

SCAN_INTERVAL_SECONDS = 60
REMINDER_WINDOW_MINUTES = 30

_reminder_task: Optional[asyncio.Task] = None


async def start_reminder_worker() -> None:
    global _reminder_task
    if _reminder_task is None:
        _reminder_task = asyncio.create_task(_reminder_loop())
        logger.info("Started deadline reminder worker.")


async def stop_reminder_worker() -> None:
    global _reminder_task
    if _reminder_task is not None:
        _reminder_task.cancel()
        try:
            await _reminder_task
        except asyncio.CancelledError:
            pass
        _reminder_task = None
        logger.info("Stopped deadline reminder worker.")


async def _reminder_loop() -> None:
    while True:
        try:
            await _scan_and_send()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("deadline reminder scan failed")
        try:
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break


async def _scan_and_send() -> None:
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(minutes=REMINDER_WINDOW_MINUTES)

    async with get_db_session() as db:
        rows = (
            await db.execute(
                select(ReflectionActivity).where(
                    ReflectionActivity.status == ActivityStatus.PUBLISHED,
                    ReflectionActivity.deadline.is_not(None),
                    ReflectionActivity.deadline_reminder_sent_at.is_(None),
                    ReflectionActivity.deadline > now,
                    ReflectionActivity.deadline <= window_end,
                )
            )
        ).scalars().all()
        # Detach by snapshotting fields we'll need outside the session.
        candidates = [
            (a.id, a.title, a.context_description, a.deadline) for a in rows
        ]

    for activity_id, title, context_description, deadline in candidates:
        try:
            await _send_reminders_for_activity(
                activity_id=activity_id,
                title=title,
                context_description=context_description,
                deadline=deadline,
            )
        except Exception:
            logger.exception(
                "Failed to send deadline reminders for activity %s", activity_id
            )


async def _send_reminders_for_activity(
    *, activity_id, title: str, context_description: str, deadline: datetime
) -> None:
    async with get_db_session() as db:
        # Re-check idempotency under fresh session in case another tick raced us.
        activity = await db.get(ReflectionActivity, activity_id)
        if activity is None or activity.deadline_reminder_sent_at is not None:
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
            # Unscoped activity — no defined cohort to remind. Mark sent so we
            # don't keep re-scanning it every minute until its deadline passes.
            activity.deadline_reminder_sent_at = datetime.now(timezone.utc)
            await db.commit()
            return

        # Exclude students whose LLM-judged "finished" flag is set. We
        # deliberately do NOT use status=EVALUATED here: a student whose
        # session was auto-evaluated on disconnect (without LLM closure) is
        # still on the hook to actually finish before the deadline.
        finalized_subq = (
            select(ChatSession.student_id)
            .where(ChatSession.activity_id == activity_id)
            .where(ChatSession.finalized_at.is_not(None))
            .distinct()
            .subquery()
        )

        recipient_rows = (
            await db.execute(
                select(User.email, User.display_name)
                .join(CourseEnrollment, CourseEnrollment.student_id == User.id)
                .where(CourseEnrollment.course_id.in_(course_ids))
                .where(User.email.is_not(None))
                .where(CourseEnrollment.student_id.not_in(select(finalized_subq)))
                .distinct()
            )
        ).all()

        # Mark BEFORE sending so a worker restart mid-loop can't double-fire.
        activity.deadline_reminder_sent_at = datetime.now(timezone.utc)
        await db.commit()

    if not recipient_rows:
        return

    link = f"{frontend_base_url()}/?activity={activity_id}"
    deadline_human = deadline.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for email, display_name in recipient_rows:
        if not email:
            continue
        greeting = f"Hi {display_name}," if display_name else "Hi,"
        body_html = (
            f"<p>{greeting}</p>"
            f"<p>Your activity <strong>{title}</strong> is due in about 30 minutes "
            f"(deadline: {deadline_human}).</p>"
            f"<p style='color:#4a6c65;'>{context_description}</p>"
            f"<p>Open the chat to finish your reflection before time runs out.</p>"
        )
        html = render_button_email(
            headline="Activity ending soon",
            body_html=body_html,
            cta_label="Open activity",
            cta_url=link,
        )
        await send_email(
            to=email,
            subject=f"Reminder: {title} is due soon",
            html=html,
        )
