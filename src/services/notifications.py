"""
Helpers for creating in-app notifications.

Two types in v1:
  - "unfinished_activity": fired when a student leaves a chat without the LLM
    marking the activity finished. Idempotent per (user, activity) — at most
    one unread row alive at a time, with `created_at` bumped on re-trigger.
  - "new_activity": fired when a teacher publishes a new activity, alongside
    the existing email blast.
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import Notification, NotificationType

logger = logging.getLogger("milo-orchestrator.notifications")


def _activity_deep_link(activity_id: UUID) -> str:
    return f"/?activity={activity_id}"


async def create_or_touch_notification(
    db: AsyncSession,
    *,
    user_id: str,
    type: str,
    activity_id: Optional[UUID],
    title: str,
    body: Optional[str],
    deep_link: str,
) -> Notification:
    """Insert a new notification, OR if an unread one with the same
    (user_id, type, activity_id) already exists, refresh its created_at so
    it surfaces at the top of the bell. Avoids duplicate "unfinished" rows
    when a student leaves and re-enters the activity multiple times.
    """
    stmt = select(Notification).where(
        Notification.user_id == user_id,
        Notification.type == type,
        Notification.activity_id == activity_id,
        Notification.read_at.is_(None),
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        existing.title = title
        existing.body = body
        existing.deep_link = deep_link
        existing.created_at = datetime.now(timezone.utc)
        return existing

    row = Notification(
        user_id=user_id,
        type=type,
        activity_id=activity_id,
        title=title,
        body=body,
        deep_link=deep_link,
    )
    db.add(row)
    return row


async def notify_unfinished_activity(
    db: AsyncSession, *, user_id: str, activity_id: UUID, activity_title: str
) -> None:
    await create_or_touch_notification(
        db,
        user_id=user_id,
        type=NotificationType.UNFINISHED_ACTIVITY.value,
        activity_id=activity_id,
        title=f"You left {activity_title!r} unfinished",
        body="Your conversation is saved. Pick up where you left off.",
        deep_link=_activity_deep_link(activity_id),
    )


async def notify_new_activity(
    db: AsyncSession, *, user_id: str, activity_id: UUID, activity_title: str
) -> None:
    await create_or_touch_notification(
        db,
        user_id=user_id,
        type=NotificationType.NEW_ACTIVITY.value,
        activity_id=activity_id,
        title=f"New activity: {activity_title}",
        body="A new reflection activity is available in one of your courses.",
        deep_link=_activity_deep_link(activity_id),
    )


async def notify_deadline_reminder(
    db: AsyncSession, *, user_id: str, activity_id: UUID, activity_title: str
) -> None:
    """Bell counterpart of the 30-min-before-deadline email reminder. Same
    idempotency guarantee as the rest: at most one unread row per
    (user, type, activity)."""
    await create_or_touch_notification(
        db,
        user_id=user_id,
        type=NotificationType.DEADLINE_REMINDER.value,
        activity_id=activity_id,
        title=f"Deadline approaching: {activity_title}",
        body="The activity is due in about 30 minutes — open the chat to finish your reflection.",
        deep_link=_activity_deep_link(activity_id),
    )


async def notify_deadline_summary(
    db: AsyncSession,
    *,
    user_id: str,
    activity_id: UUID,
    activity_title: str,
    body: str,
) -> None:
    """Bell counterpart of the teacher's deadline-summary email. The body
    is composed by the caller so the notification copy can match the email
    (e.g. counts of completed-vs-not, metrics-readiness state)."""
    await create_or_touch_notification(
        db,
        user_id=user_id,
        type=NotificationType.DEADLINE_SUMMARY.value,
        activity_id=activity_id,
        title=f"Deadline reached: {activity_title}",
        body=body,
        deep_link=f"/?activity={activity_id}&view=analytics",
    )
