from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update

from src.core.auth import AuthenticatedUser, require_http_user
from src.core.database import get_db_session
from src.core.models import Notification, User
from src.schemas.me import MeResponse, MeUpdateRequest
from src.schemas.notifications import NotificationResponse

router = APIRouter(prefix="/me", tags=["Me"])


def _serialize(user: User) -> MeResponse:
    return MeResponse(
        uid=user.id,
        email=user.email or "",
        display_name=user.display_name or "",
        role=user.role or "student",
        photo_data_url=user.photo_data_url,
    )


@router.get("", response_model=MeResponse)
async def get_me(user: AuthenticatedUser = Depends(require_http_user)):
    async with get_db_session() as db:
        row = await db.get(User, user.uid)
        if row is None:
            raise HTTPException(status_code=404, detail="User not found.")
        return _serialize(row)


@router.patch("", response_model=MeResponse)
async def update_me(
    payload: MeUpdateRequest,
    user: AuthenticatedUser = Depends(require_http_user),
):
    async with get_db_session() as db:
        row = await db.get(User, user.uid)
        if row is None:
            raise HTTPException(status_code=404, detail="User not found.")

        fields_set = payload.model_fields_set
        if "display_name" in fields_set and payload.display_name is not None:
            row.display_name = payload.display_name.strip()
        if "photo_data_url" in fields_set:
            value = payload.photo_data_url
            if value and not value.startswith("data:image/"):
                raise HTTPException(
                    status_code=400,
                    detail="photo_data_url must be a data:image/* URL.",
                )
            row.photo_data_url = value or None

        await db.flush()
        return _serialize(row)


@router.get("/notifications", response_model=List[NotificationResponse])
async def list_my_notifications(
    user: AuthenticatedUser = Depends(require_http_user),
    unread_only: bool = Query(False, description="Return only unread notifications"),
    limit: int = Query(50, ge=1, le=200),
):
    async with get_db_session() as db:
        stmt = select(Notification).where(Notification.user_id == user.uid)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
        return rows


@router.patch("/notifications/read-all", response_model=dict)
async def mark_all_my_notifications_read(
    user: AuthenticatedUser = Depends(require_http_user),
):
    async with get_db_session() as db:
        result = await db.execute(
            update(Notification)
            .where(Notification.user_id == user.uid, Notification.read_at.is_(None))
            .values(read_at=datetime.now(timezone.utc))
        )
        return {"updated": result.rowcount or 0}


@router.patch("/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_my_notification_read(
    notification_id: UUID,
    user: AuthenticatedUser = Depends(require_http_user),
):
    async with get_db_session() as db:
        row = await db.get(Notification, notification_id)
        if row is None or row.user_id != user.uid:
            raise HTTPException(status_code=404, detail="Notification not found.")
        if row.read_at is None:
            row.read_at = datetime.now(timezone.utc)
            await db.flush()
        return row
