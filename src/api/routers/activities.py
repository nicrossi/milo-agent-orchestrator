import math
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, asc, desc, exists, func, or_, select

from src.core.database import get_db_session
from src.core.auth import AuthenticatedUser, require_http_user, require_teacher
from src.core.models import (
    ActivityCourseAssignment,
    ActivityStatus,
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
    ActivityDashboardResponse, CourseRef, StudentSessionResult,
    ReflectionMetricResult, CalibrationMetricResult, TransferMetricResult,
    PaginatedStudentResults, ResultsSortBy, SortOrder,
)


async def _load_courses_for_activities(db, activity_ids):
    """Return {activity_id: [CourseRef]} for the given activities."""
    if not activity_ids:
        return {}
    rows = (
        await db.execute(
            select(ActivityCourseAssignment.activity_id, Course.id, Course.name)
            .join(Course, Course.id == ActivityCourseAssignment.course_id)
            .where(ActivityCourseAssignment.activity_id.in_(activity_ids))
        )
    ).all()
    out = {}
    for activity_id, course_id, course_name in rows:
        out.setdefault(activity_id, []).append(CourseRef(id=course_id, name=course_name))
    return out


def _attach_courses(activity, courses_map, response_cls):
    """Build a response model from an ORM activity, attaching its courses."""
    base = {
        "id": activity.id,
        "title": activity.title,
        "context_description": activity.context_description,
        "status": activity.status,
        "created_by_id": activity.created_by_id,
        "courses": courses_map.get(activity.id, []),
    }
    if response_cls is ActivityTeacherResponse:
        base["teacher_goal"] = activity.teacher_goal
    return response_cls(**base)

router = APIRouter(prefix="/activities", tags=["Activities"])

@router.post("", response_model=ActivityTeacherResponse)
async def create_activity(
    payload: ActivityCreate,
    user: AuthenticatedUser = Depends(require_teacher)
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
            await db.flush()

        courses_map = await _load_courses_for_activities(db, [activity.id])
        return _attach_courses(activity, courses_map, ActivityTeacherResponse)

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
        courses_map = await _load_courses_for_activities(db, [a.id for a in activities])
        return [_attach_courses(a, courses_map, ActivityStudentResponse) for a in activities]


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
        courses_map = await _load_courses_for_activities(db, [activity.id])
        return _attach_courses(activity, courses_map, ActivityTeacherResponse)


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
                reflection_quality=ReflectionMetricResult(
                    level=metric.reflection_quality_level,
                    justification=metric.reflection_quality_justification,
                    evidence=metric.reflection_quality_evidence,
                    recommended_action=metric.reflection_quality_action,
                ) if metric and metric.reflection_quality_level else None,
                calibration=CalibrationMetricResult(
                    level=metric.calibration_level,
                    justification=metric.calibration_justification,
                    evidence=metric.calibration_evidence,
                    recommended_action=metric.calibration_action,
                ) if metric and metric.calibration_level else None,
                contextual_transfer=TransferMetricResult(
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
