from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from src.core.auth import AuthenticatedUser, require_teacher
from src.core.database import get_db_session
from src.core.models import (
    ChatSession,
    Course,
    CourseEnrollment,
    ReflectionActivity,
    SessionMetric,
    User,
)
from src.schemas.activities import (
    CalibrationMetricResult,
    CourseRef,
    ReflectionMetricResult,
    TransferMetricResult,
)
from src.schemas.students import StudentSessionDetail, TeacherStudentResponse

router = APIRouter(prefix="/students", tags=["Students"])


@router.get("", response_model=List[TeacherStudentResponse])
async def list_students_for_teacher(
    user: AuthenticatedUser = Depends(require_teacher),
):
    """All students enrolled in any course owned by the requesting teacher."""
    async with get_db_session() as db:
        students_stmt = (
            select(User)
            .join(CourseEnrollment, CourseEnrollment.student_id == User.id)
            .join(Course, Course.id == CourseEnrollment.course_id)
            .where(Course.created_by_id == user.uid)
            .distinct()
            .order_by(User.display_name.asc())
        )
        students = (await db.execute(students_stmt)).scalars().all()

        result: List[TeacherStudentResponse] = []
        for student in students:
            courses_rows = (
                await db.execute(
                    select(Course.id, Course.name)
                    .join(CourseEnrollment, CourseEnrollment.course_id == Course.id)
                    .where(
                        CourseEnrollment.student_id == student.id,
                        Course.created_by_id == user.uid,
                    )
                    .order_by(Course.name.asc())
                )
            ).all()

            session_count = (
                await db.execute(
                    select(func.count(ChatSession.id))
                    .join(ReflectionActivity, ReflectionActivity.id == ChatSession.activity_id)
                    .where(
                        ChatSession.student_id == student.id,
                        ReflectionActivity.created_by_id == user.uid,
                    )
                )
            ).scalar_one()

            result.append(
                TeacherStudentResponse(
                    student_id=student.id,
                    display_name=student.display_name or "",
                    email=student.email or "",
                    courses=[CourseRef(id=c.id, name=c.name) for c in courses_rows],
                    session_count=session_count,
                )
            )

        return result


@router.get("/{student_id}/sessions", response_model=List[StudentSessionDetail])
async def get_student_sessions(
    student_id: str,
    user: AuthenticatedUser = Depends(require_teacher),
):
    """All sessions of a student across activities owned by the teacher."""
    async with get_db_session() as db:
        # Authz: student must be enrolled in at least one of the teacher's courses
        is_enrolled = (
            await db.execute(
                select(CourseEnrollment.course_id)
                .join(Course, Course.id == CourseEnrollment.course_id)
                .where(
                    CourseEnrollment.student_id == student_id,
                    Course.created_by_id == user.uid,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if is_enrolled is None:
            raise HTTPException(
                status_code=403,
                detail="Student is not enrolled in any of your courses.",
            )

        rows = (
            await db.execute(
                select(ChatSession, ReflectionActivity, SessionMetric)
                .join(ReflectionActivity, ReflectionActivity.id == ChatSession.activity_id)
                .outerjoin(SessionMetric, SessionMetric.session_id == ChatSession.id)
                .where(
                    ChatSession.student_id == student_id,
                    ReflectionActivity.created_by_id == user.uid,
                )
                .order_by(ChatSession.started_at.desc())
            )
        ).all()

        result: List[StudentSessionDetail] = []
        for chat_session, activity, metric in rows:
            reflection_q = None
            calibration = None
            transfer = None

            if metric is not None:
                if metric.reflection_quality_level:
                    reflection_q = ReflectionMetricResult(
                        level=metric.reflection_quality_level,
                        justification=metric.reflection_quality_justification,
                        evidence=metric.reflection_quality_evidence,
                        recommended_action=metric.reflection_quality_action,
                    )
                if metric.calibration_level:
                    calibration = CalibrationMetricResult(
                        level=metric.calibration_level,
                        justification=metric.calibration_justification,
                        evidence=metric.calibration_evidence,
                        recommended_action=metric.calibration_action,
                    )
                if metric.contextual_transfer_level:
                    transfer = TransferMetricResult(
                        level=metric.contextual_transfer_level,
                        justification=metric.contextual_transfer_justification,
                        evidence=metric.contextual_transfer_evidence,
                        recommended_action=metric.contextual_transfer_action,
                    )

            result.append(
                StudentSessionDetail(
                    session_id=chat_session.id,
                    activity_id=activity.id,
                    activity_title=activity.title,
                    status=chat_session.status,
                    started_at=chat_session.started_at,
                    reflection_quality=reflection_q,
                    calibration=calibration,
                    contextual_transfer=transfer,
                )
            )

        return result
