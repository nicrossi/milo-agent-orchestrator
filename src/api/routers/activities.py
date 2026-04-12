import math
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, func, desc, asc

from src.core.database import get_db_session
from src.core.auth import AuthenticatedUser, require_http_user, require_teacher
from src.core.models import ReflectionActivity, ChatSession, SessionMetric, User
from src.schemas.activities import (
    ActivityCreate, ActivityStudentResponse, ActivityTeacherResponse,
    ActivityDashboardResponse, StudentSessionResult, MetricResult, ActivityStatus,
    PaginatedStudentResults, ResultsSortBy, SortOrder,
)

router = APIRouter(prefix="/activities", tags=["Activities"])

@router.post("", response_model=ActivityTeacherResponse)
async def create_activity(
    payload: ActivityCreate,
    user: AuthenticatedUser = Depends(require_teacher)
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
    user: AuthenticatedUser = Depends(require_teacher),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(30, ge=1, le=100, description="Results per page (max 100)"),
    latest_per_student: bool = Query(False, description="Return only the most recent session per student"),
    sort_by: ResultsSortBy = Query(ResultsSortBy.STARTED_AT, description="Field to sort by"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort direction"),
):
    async with get_db_session() as db:
        # --- Fetch activity ---
        stmt = select(ReflectionActivity).where(ReflectionActivity.id == activity_id)
        result = await db.execute(stmt)
        activity = result.scalar_one_or_none()
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found")

        # --- Build base filter (optionally narrowed to latest session per student) ---
        if latest_per_student:
            # PostgreSQL DISTINCT ON: pick newest session per student.
            # Wrapped as subquery so pagination/sorting apply freely on top.
            latest_subq = (
                select(ChatSession.id)
                .where(ChatSession.activity_id == activity_id)
                .distinct(ChatSession.student_id)
                .order_by(ChatSession.student_id, ChatSession.started_at.desc())
            ).subquery()

            base_filter = ChatSession.id.in_(select(latest_subq.c.id))
        else:
            base_filter = ChatSession.activity_id == activity_id

        # --- Separate count query ---
        count_stmt = select(func.count()).select_from(ChatSession).where(base_filter)
        total = (await db.execute(count_stmt)).scalar_one()

        # --- Sort column mapping (extend here for future sort_by options) ---
        sort_column_map = {
            ResultsSortBy.STARTED_AT: ChatSession.started_at,
        }
        sort_col = sort_column_map[sort_by]
        order_fn = desc if sort_order == SortOrder.DESC else asc

        # --- Paginated data query ---
        offset = (page - 1) * page_size

        sessions_stmt = (
            select(ChatSession, User.display_name, SessionMetric)
            .join(User, ChatSession.student_id == User.id)
            .outerjoin(SessionMetric, ChatSession.id == SessionMetric.session_id)
            .where(base_filter)
            .order_by(order_fn(sort_col))
            .offset(offset)
            .limit(page_size)
        )
        sessions_result = await db.execute(sessions_stmt)

        items: List[StudentSessionResult] = []
        for chat_session, display_name, metric in sessions_result:
            items.append(StudentSessionResult(
                session_id=chat_session.id,
                student_id=chat_session.student_id,
                student_name=display_name,
                status=chat_session.status,
                started_at=chat_session.started_at,
                reflection_quality=MetricResult(
                    level=metric.reflection_quality_level,
                    justification=metric.reflection_quality_justification,
                    evidence=metric.reflection_quality_evidence,
                    recommended_action=metric.reflection_quality_action,
                ) if metric and metric.reflection_quality_level else None,
                calibration=MetricResult(
                    level=metric.calibration_level,
                    justification=metric.calibration_justification,
                    evidence=metric.calibration_evidence,
                    recommended_action=metric.calibration_action,
                ) if metric and metric.calibration_level else None,
                contextual_transfer=MetricResult(
                    level=metric.contextual_transfer_level,
                    justification=metric.contextual_transfer_justification,
                    evidence=metric.contextual_transfer_evidence,
                    recommended_action=metric.contextual_transfer_action,
                ) if metric and metric.contextual_transfer_level else None,
            ))

        total_pages = math.ceil(total / page_size) if total > 0 else 0

        return ActivityDashboardResponse(
            activity=ActivityTeacherResponse.model_validate(activity),
            results=PaginatedStudentResults(
                items=items,
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
            ),
        )

@router.get("/{activity_id}/transcripts/{student_id}", response_class=PlainTextResponse)
async def get_student_transcript(
    activity_id: UUID,
    student_id: str,
    user: AuthenticatedUser = Depends(require_teacher)
):
    async with get_db_session() as db:
        stmt = (
            select(ChatSession.transcript)
            .where(ChatSession.activity_id == activity_id, ChatSession.student_id == student_id)
            .where(ChatSession.transcript != "")
            .order_by(ChatSession.started_at.asc())
        )
        result = await db.execute(stmt)
        transcripts = result.scalars().all()
        
        if not transcripts:
            raise HTTPException(status_code=404, detail="No transcript found for this student in this activity.")

        full_transcript = "\n\n".join(transcripts)
        return full_transcript
