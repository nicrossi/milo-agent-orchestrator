"""
Background worker that fires two kinds of deadline notifications:

  * Student reminder: 30 minutes before the deadline, mail + bell every
    enrolled student who hasn't already LLM-finalized their session.
  * Teacher summary: at / after the deadline, mail + bell the activity's
    teacher with a who-completed / who-didn't roll-up. The CTA depends on
    whether the on-time cohort's metrics are ready to view.

Both kinds are idempotent via per-activity timestamp columns
(`deadline_reminder_sent_at`, `deadline_summary_sent_at`). The worker wakes
every SCAN_INTERVAL_SECONDS so notifications fire within at most that many
seconds of their target time.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select, update

from src.core.database import get_db_session
from src.core.models import (
    ActivityCourseAssignment,
    ActivityStatus,
    ChatSession,
    CourseEnrollment,
    ReflectionActivity,
    SessionStatus,
    User,
)
from src.services.email import frontend_base_url, render_button_email, send_email
from src.services.notifications import (
    notify_deadline_reminder,
    notify_deadline_summary,
)

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
            await _scan_student_reminders()
            await _scan_teacher_summaries()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("deadline reminder scan failed")
        try:
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break


# ─────────────────────────────────────────────────────────────────────────
# Student deadline reminder (30 min before)
# ─────────────────────────────────────────────────────────────────────────
async def _scan_student_reminders() -> None:
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
    recipient_rows: List[Tuple[str, str, str]] = []  # (user_id, email, display_name)
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
            activity.deadline_reminder_sent_at = datetime.now(timezone.utc)
            await db.commit()
            return

        # Exclude students who already LLM-finalized (deliberately not
        # status=EVALUATED — a student whose session was auto-evaluated on
        # disconnect without closure is still on the hook to actually finish).
        finalized_subq = (
            select(ChatSession.student_id)
            .where(ChatSession.activity_id == activity_id)
            .where(ChatSession.finalized_at.is_not(None))
            .distinct()
            .subquery()
        )

        recipient_rows = list(
            (
                await db.execute(
                    select(User.id, User.email, User.display_name)
                    .join(CourseEnrollment, CourseEnrollment.student_id == User.id)
                    .where(CourseEnrollment.course_id.in_(course_ids))
                    .where(CourseEnrollment.student_id.not_in(select(finalized_subq)))
                    .distinct()
                )
            ).all()
        )

        # Bell notifications go in the same DB transaction so a partial
        # send (e.g. email API failure) doesn't leave the bell out of sync
        # with the idempotency marker.
        for user_id, _email, _display_name in recipient_rows:
            await notify_deadline_reminder(
                db,
                user_id=user_id,
                activity_id=activity.id,
                activity_title=title,
            )

        # Mark BEFORE sending email so a worker restart mid-loop can't double-fire.
        activity.deadline_reminder_sent_at = datetime.now(timezone.utc)
        await db.commit()

    if not recipient_rows:
        return

    link = f"{frontend_base_url()}/?activity={activity_id}"
    deadline_human = deadline.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for _user_id, email, display_name in recipient_rows:
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


# ─────────────────────────────────────────────────────────────────────────
# Teacher deadline summary (at / after deadline)
# ─────────────────────────────────────────────────────────────────────────
async def _scan_teacher_summaries() -> None:
    now = datetime.now(timezone.utc)

    async with get_db_session() as db:
        rows = (
            await db.execute(
                select(ReflectionActivity).where(
                    ReflectionActivity.deadline.is_not(None),
                    ReflectionActivity.deadline_summary_sent_at.is_(None),
                    ReflectionActivity.deadline <= now,
                )
            )
        ).scalars().all()
        candidates = [a.id for a in rows]

    for activity_id in candidates:
        try:
            await _send_summary_for_activity(activity_id)
        except Exception:
            logger.exception(
                "Failed to send teacher deadline summary for activity %s", activity_id
            )


async def _send_summary_for_activity(activity_id) -> None:
    """Build and send the teacher's deadline-summary email + bell."""
    async with get_db_session() as db:
        activity = await db.get(ReflectionActivity, activity_id)
        if activity is None or activity.deadline_summary_sent_at is not None:
            return
        if activity.deadline is None:
            return

        teacher = await db.get(User, activity.created_by_id)
        if teacher is None:
            logger.warning(
                "Activity %s has no teacher record; skipping summary.",
                activity_id,
            )
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

        # Enrolled students for this activity's cohort.
        enrolled_rows = []
        if course_ids:
            enrolled_rows = list(
                (
                    await db.execute(
                        select(User.id, User.display_name)
                        .join(CourseEnrollment, CourseEnrollment.student_id == User.id)
                        .where(CourseEnrollment.course_id.in_(course_ids))
                        .distinct()
                    )
                ).all()
            )

        # Most recent session per (student, activity). DISTINCT ON would
        # be cleaner; a Python-side dedupe is good enough here since the
        # cohort is small.
        session_rows = list(
            (
                await db.execute(
                    select(
                        ChatSession.student_id,
                        ChatSession.status,
                        ChatSession.finalized_at,
                    )
                    .where(ChatSession.activity_id == activity_id)
                    .order_by(ChatSession.started_at.desc())
                )
            ).all()
        )
        latest_by_student = {}
        for student_id, status, finalized_at in session_rows:
            if student_id not in latest_by_student:
                latest_by_student[student_id] = (status, finalized_at)

        deadline = activity.deadline
        on_time: List[Tuple[str, str]] = []  # (display_name, status_label)
        late_or_missing: List[str] = []      # display_name
        on_time_all_evaluated = True

        for student_id, display_name in enrolled_rows:
            entry = latest_by_student.get(student_id)
            if entry is None:
                late_or_missing.append(display_name or student_id)
                continue
            status, finalized_at = entry
            if finalized_at is not None and finalized_at <= deadline:
                status_label = (
                    "Evaluated" if status == SessionStatus.EVALUATED else
                    "Evaluation failed" if status == SessionStatus.EVALUATION_FAILED else
                    "Evaluating"
                )
                if status != SessionStatus.EVALUATED:
                    on_time_all_evaluated = False
                on_time.append((display_name or student_id, status_label))
            else:
                late_or_missing.append(display_name or student_id)

        # Atomic compare-and-swap on the idempotency marker. Only the
        # worker that flips deadline_summary_sent_at from NULL to now()
        # proceeds to send. Any concurrent loop tick finds rowcount=0.
        result = await db.execute(
            update(ReflectionActivity)
            .where(ReflectionActivity.id == activity_id)
            .where(ReflectionActivity.deadline_summary_sent_at.is_(None))
            .values(deadline_summary_sent_at=datetime.now(timezone.utc))
        )
        if (result.rowcount or 0) == 0:
            await db.rollback()
            logger.info(
                "Activity %s: another worker already sent the deadline summary — skipping.",
                activity_id,
            )
            return

        # Bell notification (same transaction as the CAS).
        bell_body = _summary_bell_body(
            on_time_count=len(on_time),
            cohort_count=len(enrolled_rows),
            metrics_ready=bool(on_time) and on_time_all_evaluated,
        )
        await notify_deadline_summary(
            db,
            user_id=teacher.id,
            activity_id=activity.id,
            activity_title=activity.title,
            body=bell_body,
        )
        await db.commit()

        teacher_email = teacher.email
        teacher_name = teacher.display_name or ""
        title = activity.title

    if not teacher_email:
        return

    metrics_ready = bool(on_time) and on_time_all_evaluated
    link = f"{frontend_base_url()}/?activity={activity_id}&view=analytics"
    body_html = _summary_email_body(
        teacher_name=teacher_name,
        title=title,
        deadline=deadline,
        on_time=on_time,
        late_or_missing=late_or_missing,
        metrics_ready=metrics_ready,
        any_on_time=bool(on_time),
    )
    html = render_button_email(
        headline=f"Activity finished: {title}",
        body_html=body_html,
        cta_label="View metrics" if metrics_ready else "Open analytics",
        cta_url=link,
    ) if metrics_ready else _no_button_email(
        headline=f"Activity finished: {title}",
        body_html=body_html,
    )
    await send_email(
        to=teacher_email,
        subject=f"Activity finished: {title}",
        html=html,
    )


def _summary_bell_body(*, on_time_count: int, cohort_count: int, metrics_ready: bool) -> str:
    if cohort_count == 0:
        base = "The deadline has passed."
    else:
        base = f"{on_time_count} of {cohort_count} students completed on time."
    if not on_time_count:
        return f"{base} No students completed in time."
    if metrics_ready:
        return f"{base} Metrics are ready — open analytics."
    return f"{base} Wait for the metrics to be analyzed."


def _summary_email_body(
    *,
    teacher_name: str,
    title: str,
    deadline: datetime,
    on_time: List[Tuple[str, str]],
    late_or_missing: List[str],
    metrics_ready: bool,
    any_on_time: bool,
) -> str:
    greeting = f"Hi {teacher_name}," if teacher_name else "Hi,"
    deadline_human = deadline.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    on_time_html = (
        "".join(f"<li>{name} — <em>{status}</em></li>" for name, status in on_time)
        if on_time
        else "<li><em>No students completed on time.</em></li>"
    )
    missing_html = (
        "".join(f"<li>{name}</li>" for name in late_or_missing)
        if late_or_missing
        else "<li><em>None.</em></li>"
    )

    if not any_on_time:
        readiness = "<p><em>No students completed the activity in time.</em></p>"
    elif metrics_ready:
        readiness = "<p>Metrics are ready — click the button below to view the breakdown.</p>"
    else:
        readiness = "<p><em>Wait for the metrics to be analyzed.</em></p>"

    return (
        f"<p>{greeting}</p>"
        f"<p>The deadline for <strong>{title}</strong> has arrived "
        f"({deadline_human}). Here's the cohort summary:</p>"
        f"<p><strong>Completed on time:</strong></p>"
        f"<ul>{on_time_html}</ul>"
        f"<p><strong>Did not complete on time:</strong></p>"
        f"<ul>{missing_html}</ul>"
        f"{readiness}"
    )


def _no_button_email(*, headline: str, body_html: str) -> str:
    """Same visual frame as render_button_email but with the CTA omitted —
    used when metrics aren't ready yet so the teacher gets the summary
    without a misleading "View metrics" button."""
    return f"""\
<!doctype html>
<html lang="en">
<body style="margin:0;padding:0;background:#f4f8f6;font-family:'Segoe UI',Tahoma,Arial,sans-serif;color:#11312b;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f8f6;padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width:520px;background:#ffffff;border:1px solid #d8e8e3;border-radius:14px;padding:32px;">
          <tr>
            <td>
              <h1 style="margin:0 0 16px;font-size:1.4rem;color:#136d56;">{headline}</h1>
              <div style="font-size:0.95rem;line-height:1.5;color:#11312b;">{body_html}</div>
              <p style="margin:24px 0 0;font-size:0.8rem;color:#4a6c65;">Milo — Metacognitive Coach</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
