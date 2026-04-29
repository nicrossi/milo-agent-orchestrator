from fastapi import APIRouter, Depends, HTTPException

from src.core.auth import AuthenticatedUser, require_http_user
from src.core.database import get_db_session
from src.core.models import User
from src.schemas.me import MeResponse, MeUpdateRequest

router = APIRouter(prefix="/me", tags=["Me"])


def _serialize(user: User) -> MeResponse:
    return MeResponse(
        uid=user.id,
        email=user.email or "",
        display_name=user.display_name or "",
        role=user.role or "student",
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
        row.display_name = payload.display_name.strip()
        await db.flush()
        return _serialize(row)
