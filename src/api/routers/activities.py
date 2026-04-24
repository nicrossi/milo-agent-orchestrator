from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, exists, or_, select

from src.core.database import get_db_session
from src.core.auth import AuthenticatedUser, require_http_user
from src.core.models import (
    ActivityCourseAssignment,
    ChatSession,
    Course,
    CourseEnrollment,
    ReflectionActivity,
    SessionMetric,
    User,
)
from src.schemas.activities import (
    ActivityAssignCoursesRequest,
    ActivityCreate, ActivityStudentResponse, ActivityTeacherResponse,
    ActivityDashboardResponse, StudentSessionResult, MetricResult, ActivityStatus
)

router = APIRouter(prefix="/activities", tags=["Activities"])

@router.post("", response_model=ActivityTeacherResponse)
async def create_activity(
    payload: ActivityCreate,
    user: AuthenticatedUser = Depends(require_http_user)
):
    async with get_db_session() as db:
        if payload.course_ids:
            course_rows = await db.execute(
                select(Course.id).where(Course.id.in_(payload.course_ids))
            )
            found_course_ids = {row[0] for row in course_rows.all()}
            missing = [str(course_id) for course_id in payload.course_ids if course_id not in found_course_ids]
            if missing:
                raise HTTPException(
                    status_code=404,
                    detail=f"Some courses were not found: {', '.join(missing)}",
                )

        activity = ReflectionActivity(
            title=payload.title,
            teacher_goal=payload.teacher_goal,
            context_description=payload.context_description,
            status=payload.status,
            created_by_id=user.uid
        )
        db.add(activity)
        await db.flush()

        if payload.course_ids:
            for course_id in payload.course_ids:
                db.add(
                    ActivityCourseAssignment(
                        activity_id=activity.id,
                        course_id=course_id,
                        assigned_by_id=user.uid,
                    )
                )

        return activity

@router.get("", response_model=List[ActivityStudentResponse])
async def list_published_activities(
    user: AuthenticatedUser = Depends(require_http_user)
):
    async with get_db_session() as db:
        assignments_exist = exists(
            select(ActivityCourseAssignment.activity_id).where(
                ActivityCourseAssignment.activity_id == ReflectionActivity.id
            )
        )
        student_has_assignment = exists(
            select(ActivityCourseAssignment.activity_id)
            .join(
                CourseEnrollment,
                CourseEnrollment.course_id == ActivityCourseAssignment.course_id,
            )
            .where(
                and_(
                    ActivityCourseAssignment.activity_id == ReflectionActivity.id,
                    CourseEnrollment.student_id == user.uid,
                )
            )
        )

        stmt = (
            select(ReflectionActivity)
            .where(ReflectionActivity.status == ActivityStatus.PUBLISHED)
            .where(
                or_(
                    ReflectionActivity.created_by_id == user.uid,
                    student_has_assignment,
                    ~assignments_exist,
                )
            )
            .order_by(ReflectionActivity.id.desc())
        )
        result = await db.execute(stmt)
        activities = result.scalars().all()
        return activities


@router.post("/{activity_id}/assign-courses", response_model=ActivityTeacherResponse)
async def assign_activity_to_courses(
    activity_id: UUID,
    payload: ActivityAssignCoursesRequest,
    user: AuthenticatedUser = Depends(require_http_user),
):
    async with get_db_session() as db:
        activity = await db.get(ReflectionActivity, activity_id)
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found")

        course_rows = await db.execute(
            select(Course.id).where(Course.id.in_(payload.course_ids))
        )
        found_course_ids = {row[0] for row in course_rows.all()}
        missing = [str(course_id) for course_id in payload.course_ids if course_id not in found_course_ids]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"Some courses were not found: {', '.join(missing)}",
            )

        existing_rows = await db.execute(
            select(ActivityCourseAssignment.course_id).where(
                and_(
                    ActivityCourseAssignment.activity_id == activity_id,
                    ActivityCourseAssignment.course_id.in_(payload.course_ids),
                )
            )
        )
        existing_ids = {row[0] for row in existing_rows.all()}

        for course_id in payload.course_ids:
            if course_id in existing_ids:
                continue
            db.add(
                ActivityCourseAssignment(
                    activity_id=activity_id,
                    course_id=course_id,
                    assigned_by_id=user.uid,
                )
            )

        await db.flush()
        return activity


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

        return ActivityDashboardResponse(
            activity=ActivityTeacherResponse.model_validate(activity),
            results=results
        )
