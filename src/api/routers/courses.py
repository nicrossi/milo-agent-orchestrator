from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, select

from src.core.auth import AuthenticatedUser, require_http_user
from src.core.database import get_db_session
from src.core.models import Course, CourseEnrollment, User
from src.schemas.courses import (
    AddStudentRequest,
    CourseCreate,
    CourseDetailResponse,
    CourseResponse,
    CourseStudentResponse,
)

router = APIRouter(prefix="/courses", tags=["Courses"])


@router.post("", response_model=CourseResponse)
async def create_course(
    payload: CourseCreate,
    user: AuthenticatedUser = Depends(require_http_user),
):
    async with get_db_session() as db:
        course = Course(
            name=payload.name.strip(),
            description=(payload.description or "").strip() or None,
            created_by_id=user.uid,
        )
        db.add(course)
        await db.flush()
        return course


@router.get("", response_model=List[CourseResponse])
async def list_courses(
    user: AuthenticatedUser = Depends(require_http_user),
):
    async with get_db_session() as db:
        stmt = (
            select(Course)
            .outerjoin(
                CourseEnrollment,
                CourseEnrollment.course_id == Course.id,
            )
            .where(
                or_(
                    Course.created_by_id == user.uid,
                    CourseEnrollment.student_id == user.uid,
                )
            )
            .order_by(Course.created_at.desc())
        )
        result = await db.execute(stmt)
        # unique() avoids duplicates when user is both creator and enrolled.
        return list(result.scalars().unique().all())


@router.post("/{course_id}/students", response_model=CourseDetailResponse)
async def add_student_to_course(
    course_id: UUID,
    payload: AddStudentRequest,
    user: AuthenticatedUser = Depends(require_http_user),
):
    student_id = payload.student_id.strip()
    async with get_db_session() as db:
        course = await db.get(Course, course_id)
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        student = await db.get(User, student_id)
        if not student:
            raise HTTPException(status_code=404, detail="Student user not found")

        existing_stmt = select(CourseEnrollment).where(
            and_(
                CourseEnrollment.course_id == course_id,
                CourseEnrollment.student_id == student_id,
            )
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing is None:
            db.add(
                CourseEnrollment(
                    course_id=course_id,
                    student_id=student_id,
                    added_by_id=user.uid,
                )
            )
            await db.flush()

        return await _build_course_detail(db, course_id)


@router.get("/{course_id}", response_model=CourseDetailResponse)
async def get_course_detail(
    course_id: UUID,
    user: AuthenticatedUser = Depends(require_http_user),
):
    async with get_db_session() as db:
        course = await db.get(Course, course_id)
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        return await _build_course_detail(db, course_id)


async def _build_course_detail(db, course_id: UUID) -> CourseDetailResponse:
    course = await db.get(Course, course_id)
    students_stmt = (
        select(User.id, User.display_name, User.email)
        .join(CourseEnrollment, CourseEnrollment.student_id == User.id)
        .where(CourseEnrollment.course_id == course_id)
        .order_by(User.display_name.asc())
    )
    students_rows = (await db.execute(students_stmt)).all()
    students = [
        CourseStudentResponse(
            student_id=row.id,
            display_name=row.display_name,
            email=row.email,
        )
        for row in students_rows
    ]
    return CourseDetailResponse(course=CourseResponse.model_validate(course), students=students)

