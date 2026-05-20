import logging
import math
from datetime import datetime, timezone
from typing import List
from uuid import UUID, uuid4
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, asc, desc, exists, func, or_, select

from src.core.database import get_db_session
from src.core.auth import AuthenticatedUser, require_http_user, require_teacher
from src.core.models import (
    ActivityCourseAssignment,
    ActivityFile,
    ActivityFileStatus,
    ActivityStatus,
    ChatSession,
    Course,
    CourseEnrollment,
    ReflectionActivity,
    SessionMetric,
    User,
)
from src.services import s3 as s3_service
from src.services.email import frontend_base_url, render_button_email, send_email
from src.services.notifications import notify_new_activity

logger = logging.getLogger("milo-orchestrator.activities")
from src.schemas.activities import (
    ActivityAssignCoursesRequest,
    ActivityCreate, ActivityStudentResponse, ActivityTeacherResponse,
    ActivityDashboardResponse, CourseRef, StudentSessionRef, StudentSessionResult,
    ReflectionMetricResult, CalibrationMetricResult, TransferMetricResult,
    PaginatedStudentResults, ResultsSortBy, SortOrder, ActivityResetRequest,
    ActivityUpdate,
    ActivityFileCreateRequest, ActivityFilePresignResponse, ActivityFileResponse,
    MAX_FILES_PER_ACTIVITY,
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


async def _load_activity_stats(db, activity_ids):
    """Return {activity_id: {"completed": int, "assigned": int}} batched.

    - completed: chat_sessions for the activity with finalized_at IS NOT NULL
    - assigned:  distinct students enrolled in any course this activity is
                 assigned to. 0 when the activity isn't assigned to any
                 course (i.e. it's a global/unscoped activity).
    """
    if not activity_ids:
        return {}

    completed_rows = (
        await db.execute(
            select(ChatSession.activity_id, func.count())
            .where(ChatSession.activity_id.in_(activity_ids))
            .where(ChatSession.finalized_at.is_not(None))
            .group_by(ChatSession.activity_id)
        )
    ).all()
    completed_map = {aid: int(count) for aid, count in completed_rows}

    assigned_rows = (
        await db.execute(
            select(
                ActivityCourseAssignment.activity_id,
                func.count(func.distinct(CourseEnrollment.student_id)),
            )
            .join(
                CourseEnrollment,
                CourseEnrollment.course_id == ActivityCourseAssignment.course_id,
            )
            .where(ActivityCourseAssignment.activity_id.in_(activity_ids))
            .group_by(ActivityCourseAssignment.activity_id)
        )
    ).all()
    assigned_map = {aid: int(count) for aid, count in assigned_rows}

    return {
        aid: {
            "completed": completed_map.get(aid, 0),
            "assigned": assigned_map.get(aid, 0),
        }
        for aid in activity_ids
    }


async def _load_student_sessions(db, activity_ids, student_id):
    """Return {activity_id: StudentSessionRef} for the student's most recent
    session per activity. Activities without a session for this student are
    absent from the dict so the response renders as "Start reflection".
    """
    if not activity_ids:
        return {}
    rows = (
        await db.execute(
            select(ChatSession)
            .where(ChatSession.activity_id.in_(activity_ids))
            .where(ChatSession.student_id == student_id)
            .distinct(ChatSession.activity_id)
            .order_by(ChatSession.activity_id, ChatSession.started_at.desc())
        )
    ).scalars().all()
    return {
        s.activity_id: StudentSessionRef(
            id=s.id,
            status=s.status,
            started_at=s.started_at,
            finalized_at=s.finalized_at,
        )
        for s in rows
    }


def _attach_courses(
    activity,
    courses_map,
    response_cls,
    sessions_map=None,
    requester_uid=None,
    stats_map=None,
):
    """Build a response model from an ORM activity, attaching its courses
    and (for student responses) the requesting user's session ref.

    `stats_map` is the per-activity {completed, assigned} dict produced by
    _load_activity_stats. Counts are only attached when the requester owns
    the activity, so non-teacher callers don't see roster totals.
    """
    base = {
        "id": activity.id,
        "title": activity.title,
        "context_description": activity.context_description,
        "status": activity.status,
        "created_by_id": activity.created_by_id,
        "created_at": getattr(activity, "created_at", None),
        "deadline": activity.deadline,
        "courses": courses_map.get(activity.id, []),
    }
    is_owner = (
        requester_uid is not None and activity.created_by_id == requester_uid
    )
    if response_cls is ActivityTeacherResponse:
        base["teacher_goal"] = activity.teacher_goal
        if stats_map is not None:
            stats = stats_map.get(activity.id) or {}
            base["completed_count"] = stats.get("completed")
            base["assigned_count"] = stats.get("assigned")
    elif response_cls is ActivityStudentResponse:
        if sessions_map is not None:
            base["student_session"] = sessions_map.get(activity.id)
        # Owners get teacher_goal so the edit form can prefill; others don't.
        if is_owner:
            base["teacher_goal"] = activity.teacher_goal
        # Same gating for the aggregate counts — teachers see the roster
        # totals on their own activities; students don't.
        if is_owner and stats_map is not None:
            stats = stats_map.get(activity.id) or {}
            base["completed_count"] = stats.get("completed")
            base["assigned_count"] = stats.get("assigned")
    return response_cls(**base)

async def _notify_students_of_new_activity(activity_id: UUID) -> None:
    """Background task: send a "new activity available" email to every student
    enrolled in any course this activity is assigned to. Runs after the
    response has been returned. Silently no-ops if email is not configured."""
    try:
        async with get_db_session() as db:
            activity = await db.get(ReflectionActivity, activity_id)
            if not activity:
                return

            course_ids_rows = (
                await db.execute(
                    select(ActivityCourseAssignment.course_id).where(
                        ActivityCourseAssignment.activity_id == activity_id
                    )
                )
            ).all()
            course_ids = [row[0] for row in course_ids_rows]
            if not course_ids:
                return  # Activity not scoped to any course → no recipients.

            recipient_rows = (
                await db.execute(
                    select(User.id, User.email, User.display_name)
                    .join(CourseEnrollment, CourseEnrollment.student_id == User.id)
                    .where(CourseEnrollment.course_id.in_(course_ids))
                    .distinct()
                )
            ).all()

            # Create one in-app notification per enrolled student (idempotent).
            for user_id, _email, _display_name in recipient_rows:
                await notify_new_activity(
                    db,
                    user_id=user_id,
                    activity_id=activity.id,
                    activity_title=activity.title,
                )
            await db.commit()

            activity_title = activity.title
            activity_description = activity.context_description

        if not recipient_rows:
            return

        link = f"{frontend_base_url()}/?activity={activity_id}"
        for _user_id, email, display_name in recipient_rows:
            if not email:
                continue
            greeting = f"Hi {display_name}," if display_name else "Hi,"
            body_html = (
                f"<p>{greeting}</p>"
                f"<p>A new activity has been published in one of your courses:</p>"
                f"<p><strong>{activity_title}</strong></p>"
                f"<p style='color:#4a6c65;'>{activity_description}</p>"
                f"<p>Open it whenever you're ready to start your reflection.</p>"
            )
            html = render_button_email(
                headline="New activity available",
                body_html=body_html,
                cta_label="Open activity",
                cta_url=link,
            )
            await send_email(
                to=email,
                subject=f"New activity: {activity_title}",
                html=html,
            )
    except Exception:
        logger.exception("Failed to send new-activity emails for activity %s.", activity_id)


router = APIRouter(prefix="/activities", tags=["Activities"])

@router.post("", response_model=ActivityTeacherResponse)
async def create_activity(
    payload: ActivityCreate,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(require_teacher),
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
            created_by_id=user.uid,
            deadline=payload.deadline,
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
        response = _attach_courses(activity, courses_map, ActivityTeacherResponse)

    # Notify enrolled students after response is sent (only if assigned to courses
    # and only when published — drafts shouldn't trigger emails).
    if payload.course_ids and payload.status == ActivityStatus.PUBLISHED:
        background_tasks.add_task(_notify_students_of_new_activity, activity.id)
    return response

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

        # Owners see all their own activities (any status) so they can manage
        # drafts. Non-owners see only PUBLISHED activities scoped to their
        # course enrollment.
        stmt = (
            select(ReflectionActivity)
            .where(
                or_(
                    ReflectionActivity.created_by_id == user.uid,
                    and_(
                        ReflectionActivity.status == ActivityStatus.PUBLISHED,
                        or_(student_has_assignment, ~assignments_exist),
                    ),
                )
            )
            # created_at first so the picker defaults to "most recently
            # published"; id desc as a stable tiebreaker for legacy rows
            # backfilled to the same migration timestamp.
            .order_by(ReflectionActivity.created_at.desc(), ReflectionActivity.id.desc())
        )
        result = await db.execute(stmt)
        activities = result.scalars().all()
        activity_ids = [a.id for a in activities]
        courses_map = await _load_courses_for_activities(db, activity_ids)
        sessions_map = await _load_student_sessions(db, activity_ids, user.uid)
        # Roster + completion counts. _attach_courses only surfaces them to
        # the activity owner so non-teacher callers see Optional[int] = None.
        owned_ids = [a.id for a in activities if a.created_by_id == user.uid]
        stats_map = await _load_activity_stats(db, owned_ids)
        return [
            _attach_courses(
                a,
                courses_map,
                ActivityStudentResponse,
                sessions_map,
                requester_uid=user.uid,
                stats_map=stats_map,
            )
            for a in activities
        ]


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


@router.patch("/{activity_id}", response_model=ActivityTeacherResponse)
async def update_activity(
    activity_id: UUID,
    payload: ActivityUpdate,
    user: AuthenticatedUser = Depends(require_teacher),
):
    async with get_db_session() as db:
        activity = await db.get(ReflectionActivity, activity_id)
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found")
        if activity.created_by_id != user.uid:
            raise HTTPException(status_code=403, detail="Only the owner can edit this activity")

        updates = payload.model_dump(exclude_unset=True)
        non_status_fields = {k: v for k, v in updates.items() if k != "status"}
        # Field edits are allowed only while the activity is unpublished.
        # Status toggles (publish/unpublish/archive) are always allowed.
        if non_status_fields and activity.status != ActivityStatus.DRAFT:
            raise HTTPException(
                status_code=409,
                detail="Activity must be unpublished before its fields can be edited",
            )

        for field, value in updates.items():
            setattr(activity, field, value)

        await db.flush()
        courses_map = await _load_courses_for_activities(db, [activity.id])
        return _attach_courses(activity, courses_map, ActivityTeacherResponse)


@router.delete("/{activity_id}", status_code=204)
async def delete_activity(
    activity_id: UUID,
    user: AuthenticatedUser = Depends(require_teacher),
):
    from sqlalchemy import delete
    from src.core.models import ChatMessage

    async with get_db_session() as db:
        activity = await db.get(ReflectionActivity, activity_id)
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found")
        if activity.created_by_id != user.uid:
            raise HTTPException(status_code=403, detail="Only the owner can delete this activity")
        if activity.status != ActivityStatus.DRAFT:
            raise HTTPException(
                status_code=409,
                detail="Activity must be unpublished before it can be deleted",
            )

        # ChatSession.activity_id has no ON DELETE CASCADE, so wipe sessions
        # (and their messages + metrics) explicitly before removing the
        # activity. activity_course_assignments and notifications cascade.
        session_ids = [
            row[0]
            for row in (
                await db.execute(
                    select(ChatSession.id).where(ChatSession.activity_id == activity_id)
                )
            ).all()
        ]
        if session_ids:
            await db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
            await db.execute(delete(SessionMetric).where(SessionMetric.session_id.in_(session_ids)))
            await db.execute(delete(ChatSession).where(ChatSession.id.in_(session_ids)))

        await db.delete(activity)
        await db.commit()
    return None


async def _load_owned_draft_activity(db, activity_id: UUID, user: AuthenticatedUser) -> ReflectionActivity:
    """Load activity, enforce ownership and DRAFT status. Used for file
    mutations — same rule as field edits in update_activity()."""
    activity = await db.get(ReflectionActivity, activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    if activity.created_by_id != user.uid:
        raise HTTPException(status_code=403, detail="Only the owner can manage activity files")
    if activity.status != ActivityStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail="Activity must be unpublished before its files can be modified",
        )
    return activity


@router.post("/{activity_id}/files", response_model=ActivityFilePresignResponse)
async def request_activity_file_upload(
    activity_id: UUID,
    payload: ActivityFileCreateRequest,
    user: AuthenticatedUser = Depends(require_teacher),
):
    """Step 2 of the upload flow: issue a presigned PUT URL the frontend
    uses to upload directly to S3 with the activity_id baked into object
    metadata. milo-ingest will pick the object up via SQS and embed it."""
    async with get_db_session() as db:
        activity = await _load_owned_draft_activity(db, activity_id, user)

        existing_count = (
            await db.execute(
                select(func.count(ActivityFile.id)).where(
                    ActivityFile.activity_id == activity.id
                )
            )
        ).scalar_one()
        if existing_count >= MAX_FILES_PER_ACTIVITY:
            raise HTTPException(
                status_code=409,
                detail=f"Maximum {MAX_FILES_PER_ACTIVITY} files per activity",
            )

        file_id = uuid4()
        s3_key = f"activities/{activity.id}/{file_id}/{payload.filename}"
        metadata = {
            "activity-id": str(activity.id),
            "file-id": str(file_id),
            "uploaded-by": user.uid,
        }

        row = ActivityFile(
            id=file_id,
            activity_id=activity.id,
            uploaded_by_id=user.uid,
            filename=payload.filename,
            s3_key=s3_key,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
            status=ActivityFileStatus.PENDING.value,
        )
        db.add(row)
        await db.flush()

        try:
            upload_url = s3_service.generate_presigned_put(
                key=s3_key,
                content_type=payload.content_type,
                content_length=payload.size_bytes,
                metadata=metadata,
            )
        except Exception:
            logger.exception("Failed to generate presigned PUT for activity %s", activity.id)
            raise HTTPException(status_code=502, detail="Could not issue upload URL")

        required_headers = {
            "Content-Type": payload.content_type,
            "x-amz-meta-activity-id": metadata["activity-id"],
            "x-amz-meta-file-id": metadata["file-id"],
            "x-amz-meta-uploaded-by": metadata["uploaded-by"],
        }
        return ActivityFilePresignResponse(
            file_id=file_id,
            upload_url=upload_url,
            method="PUT",
            required_headers=required_headers,
            expires_in=900,
        )


@router.post(
    "/{activity_id}/files/{file_id}/confirm",
    response_model=ActivityFileResponse,
)
async def confirm_activity_file_upload(
    activity_id: UUID,
    file_id: UUID,
    user: AuthenticatedUser = Depends(require_teacher),
):
    """Verifies the object landed in S3 with the expected metadata and size,
    then flips the row to UPLOADED. milo-ingest's SQS-driven embedding job
    runs independently and may already have completed."""
    async with get_db_session() as db:
        activity = await _load_owned_draft_activity(db, activity_id, user)

        row = await db.get(ActivityFile, file_id)
        if not row or row.activity_id != activity.id:
            raise HTTPException(status_code=404, detail="File not found")

        head = s3_service.head_object(row.s3_key)
        if head is None:
            raise HTTPException(status_code=409, detail="Upload not found in S3 yet")

        s3_metadata = {k.lower(): v for k, v in (head.get("Metadata") or {}).items()}
        if s3_metadata.get("activity-id") != str(activity.id):
            raise HTTPException(status_code=409, detail="Uploaded object metadata mismatch")
        if int(head.get("ContentLength", -1)) != row.size_bytes:
            raise HTTPException(status_code=409, detail="Uploaded object size mismatch")

        row.status = ActivityFileStatus.UPLOADED.value
        row.confirmed_at = datetime.now(timezone.utc)
        await db.flush()
        return ActivityFileResponse.model_validate(row)


@router.get(
    "/{activity_id}/files",
    response_model=List[ActivityFileResponse],
)
async def list_activity_files(
    activity_id: UUID,
    user: AuthenticatedUser = Depends(require_teacher),
):
    """Owner-only list of UPLOADED files for the activity."""
    async with get_db_session() as db:
        activity = await db.get(ReflectionActivity, activity_id)
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found")
        if activity.created_by_id != user.uid:
            raise HTTPException(status_code=403, detail="Only the owner can view activity files")

        rows = (
            await db.execute(
                select(ActivityFile)
                .where(
                    and_(
                        ActivityFile.activity_id == activity.id,
                        ActivityFile.status == ActivityFileStatus.UPLOADED.value,
                    )
                )
                .order_by(ActivityFile.created_at.asc())
            )
        ).scalars().all()
        return [ActivityFileResponse.model_validate(r) for r in rows]


@router.delete(
    "/{activity_id}/files/{file_id}",
    status_code=204,
)
async def delete_activity_file(
    activity_id: UUID,
    file_id: UUID,
    user: AuthenticatedUser = Depends(require_teacher),
):
    """Delete the S3 object and the metadata row. milo-ingest reacts to the
    S3 ObjectRemoved event and clears the matching embedding rows."""
    async with get_db_session() as db:
        activity = await _load_owned_draft_activity(db, activity_id, user)

        row = await db.get(ActivityFile, file_id)
        if not row or row.activity_id != activity.id:
            raise HTTPException(status_code=404, detail="File not found")

        try:
            s3_service.delete_object(row.s3_key)
        except Exception:
            raise HTTPException(status_code=502, detail="Failed to delete file from storage")

        await db.delete(row)
    return None


@router.post("/{activity_id}/reset", response_model=ActivityTeacherResponse)
async def reset_activity(
    activity_id: UUID,
    payload: ActivityResetRequest,
    user: AuthenticatedUser = Depends(require_teacher),
):
    from sqlalchemy import delete
    from datetime import datetime, timezone, timedelta
    from src.core.models import ChatMessage
    
    async with get_db_session() as db:
        activity = await db.get(ReflectionActivity, activity_id)
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found")

        # Update the deadline to 30 days from today
        activity.deadline = datetime.now(timezone.utc) + timedelta(days=30)
        activity.deadline_reminder_sent_at = None
        activity.deadline_summary_sent_at = None

        # Find sessions to delete
        session_stmt = select(ChatSession.id).where(ChatSession.activity_id == activity_id)
        if payload.student_id:
            session_stmt = session_stmt.where(ChatSession.student_id == payload.student_id)
        
        result = await db.execute(session_stmt)
        session_ids = [row[0] for row in result.all()]

        if session_ids:
            # Delete messages
            await db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
            # Delete metrics
            await db.execute(delete(SessionMetric).where(SessionMetric.session_id.in_(session_ids)))
            # Delete sessions
            await db.execute(delete(ChatSession).where(ChatSession.id.in_(session_ids)))

        await db.commit()

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

        # --- Attach courses + completion stats to the activity payload so
        # the analytics header / picker share the same fields as the list ---
        courses_map = await _load_courses_for_activities(db, [activity.id])
        stats_map = await _load_activity_stats(db, [activity.id])
        activity_response = _attach_courses(
            activity,
            courses_map,
            ActivityTeacherResponse,
            requester_uid=user.uid,
            stats_map=stats_map,
        )

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
                finalized_at=chat_session.finalized_at,
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
                confidence_score=metric.confidence_score if metric else None,
                confidence_justification=metric.confidence_justification if metric else None,
                confidence_evidence=metric.confidence_evidence if metric else None,
            ))

        total_pages = math.ceil(total / page_size) if total > 0 else 0

        return ActivityDashboardResponse(
            activity=activity_response,
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
