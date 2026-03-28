from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.core.database import get_db_session
from src.core.auth import AuthenticatedUser, require_http_user
from src.core.models import ReflectionActivity, ChatSession, SessionMetric, User
from src.schemas.activities import (
    ActivityCreate, ActivityStudentResponse, ActivityTeacherResponse,
    ActivityDashboardResponse, StudentSessionResult, ActivityStatus
)

router = APIRouter(prefix="/activities", tags=["Activities"])

@router.post("", response_model=ActivityTeacherResponse)
async def create_activity(
    payload: ActivityCreate,
    user: AuthenticatedUser = Depends(require_http_user)
):
    async with get_db_session() as db:
        activity = ReflectionActivity(
            title=payload.title,
            teacher_goal=payload.teacher_goal,
            context_description=payload.context_description,
            status=payload.status,
            created_by_id=user.uid
        )
        db.add(activity)
        await db.flush()
        return activity

@router.get("", response_model=List[ActivityStudentResponse])
async def list_published_activities(
    user: AuthenticatedUser = Depends(require_http_user)
):
    async with get_db_session() as db:
        stmt = select(ReflectionActivity).where(ReflectionActivity.status == ActivityStatus.PUBLISHED)
        result = await db.execute(stmt)
        activities = result.scalars().all()
        return activities

@router.get("/{activity_id}/results", response_model=ActivityDashboardResponse)
async def get_activity_results(
    activity_id: UUID,
    user: AuthenticatedUser = Depends(require_http_user)
):
    async with get_db_session() as db:
        stmt = select(ReflectionActivity).where(ReflectionActivity.id == activity_id)
        result = await db.execute(stmt)
        activity = result.scalar_one_or_none()
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found")

        # Join chat sessions, users, and session metrics
        sessions_stmt = (
            select(ChatSession, User.display_name, SessionMetric)
            .join(User, ChatSession.student_id == User.id)
            .outerjoin(SessionMetric, ChatSession.id == SessionMetric.session_id)
            .where(ChatSession.activity_id == activity_id)
        )
        sessions_result = await db.execute(sessions_stmt)
        
        results = []
        for chat_session, display_name, metric in sessions_result:
            results.append(StudentSessionResult(
                session_id=chat_session.id,
                student_id=chat_session.student_id,
                student_name=display_name,
                status=chat_session.status,
                started_at=chat_session.started_at,
                dors_level=metric.dors_level if metric else None,
                dors_score=metric.dors_score if metric else None,
                goal_status=metric.goal_status if metric else None,
                goal_score=metric.goal_score if metric else None,
                evidence_quote=metric.evidence_quote if metric else None
            ))

        return ActivityDashboardResponse(
            activity=ActivityTeacherResponse.model_validate(activity),
            results=results
        )
