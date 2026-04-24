"""
Open admin dashboard endpoints — NO AUTH.

Intended for local development and manual testing only. Mounted as a sibling of
the regular routers. Do NOT enable in any internet-facing deployment.
"""
import logging
import secrets
import string
from typing import List
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from firebase_admin import auth as fb_auth
from pathlib import Path
from sqlalchemy import select, text

from src.core.auth import _ensure_firebase_app
from src.core.database import get_db_session
from src.core.models import Course, CourseEnrollment, User
from src.schemas.admin import (
    AdminCourseCreate,
    AdminCourseResponse,
    AdminEnrollRequest,
    AdminTransferTeacherRequest,
    AdminUserCreate,
    AdminUserResponse,
)

logger = logging.getLogger("milo-orchestrator.admin")

router = APIRouter(prefix="/admin", tags=["Admin (open, dev only)"])

DASHBOARD_HTML = Path(__file__).resolve().parents[2] / "static" / "admin.html"


def _random_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.get("", include_in_schema=False)
async def serve_dashboard():
    if not DASHBOARD_HTML.exists():
        raise HTTPException(status_code=500, detail=f"Dashboard HTML missing at {DASHBOARD_HTML}")
    return FileResponse(DASHBOARD_HTML, media_type="text/html")


@router.get("/users", response_model=List[AdminUserResponse])
async def list_users():
    async with get_db_session() as db:
        rows = (await db.execute(select(User).order_by(User.display_name.asc()))).scalars().all()
        return [
            AdminUserResponse(uid=u.id, email=u.email, display_name=u.display_name, password=None)
            for u in rows
        ]


@router.post("/users", response_model=AdminUserResponse)
async def create_user(payload: AdminUserCreate):
    """Creates a Firebase Auth user (with email + password) and seeds the relational users row."""
    _ensure_firebase_app()
    password = payload.password or _random_password()
    try:
        fb_user = fb_auth.create_user(
            email=str(payload.email),
            password=password,
            display_name=payload.display_name,
            email_verified=True,
        )
    except fb_auth.EmailAlreadyExistsError:
        raise HTTPException(status_code=409, detail="Email already exists in Firebase Auth.")
    except Exception as e:
        logger.error("Firebase user creation failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Firebase user creation failed: {e}")

    async with get_db_session() as db:
        await db.execute(
            text(
                """
                INSERT INTO users (id, email, display_name)
                VALUES (:id, :email, :display_name)
                ON CONFLICT (id) DO UPDATE
                SET email = EXCLUDED.email,
                    display_name = EXCLUDED.display_name
                """
            ),
            {"id": fb_user.uid, "email": str(payload.email), "display_name": payload.display_name},
        )

    return AdminUserResponse(
        uid=fb_user.uid,
        email=str(payload.email),
        display_name=payload.display_name,
        password=password,
    )


async def _build_course_response(db, course: Course) -> AdminCourseResponse:
    teacher = await db.get(User, course.created_by_id)
    student_rows = (
        await db.execute(
            select(CourseEnrollment.student_id).where(CourseEnrollment.course_id == course.id)
        )
    ).scalars().all()
    return AdminCourseResponse(
        id=course.id,
        name=course.name,
        description=course.description,
        teacher_id=course.created_by_id,
        teacher_name=teacher.display_name if teacher else None,
        teacher_email=teacher.email if teacher else None,
        student_ids=list(student_rows),
        created_at=course.created_at,
    )


@router.get("/courses", response_model=List[AdminCourseResponse])
async def list_courses():
    async with get_db_session() as db:
        courses = (
            await db.execute(select(Course).order_by(Course.created_at.desc()))
        ).scalars().all()
        return [await _build_course_response(db, c) for c in courses]


@router.post("/courses", response_model=AdminCourseResponse)
async def create_course(payload: AdminCourseCreate):
    async with get_db_session() as db:
        teacher = await db.get(User, payload.teacher_id.strip())
        if teacher is None:
            raise HTTPException(status_code=404, detail="Teacher user not found.")
        course = Course(
            name=payload.name.strip(),
            description=(payload.description or "").strip() or None,
            created_by_id=teacher.id,
        )
        db.add(course)
        await db.flush()
        return await _build_course_response(db, course)


@router.post("/courses/{course_id}/students", response_model=AdminCourseResponse)
async def enroll_student(course_id: UUID, payload: AdminEnrollRequest):
    student_id = payload.student_id.strip()
    async with get_db_session() as db:
        course = await db.get(Course, course_id)
        if course is None:
            raise HTTPException(status_code=404, detail="Course not found.")
        student = await db.get(User, student_id)
        if student is None:
            raise HTTPException(status_code=404, detail="Student user not found.")

        existing = await db.execute(
            select(CourseEnrollment).where(
                CourseEnrollment.course_id == course_id,
                CourseEnrollment.student_id == student_id,
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(
                CourseEnrollment(
                    course_id=course_id,
                    student_id=student_id,
                    added_by_id=course.created_by_id,
                )
            )
            await db.flush()
        return await _build_course_response(db, course)


@router.delete("/courses/{course_id}/students/{student_id}", response_model=AdminCourseResponse)
async def unenroll_student(course_id: UUID, student_id: str):
    async with get_db_session() as db:
        course = await db.get(Course, course_id)
        if course is None:
            raise HTTPException(status_code=404, detail="Course not found.")
        await db.execute(
            text(
                "DELETE FROM course_enrollments "
                "WHERE course_id = :cid AND student_id = :sid"
            ),
            {"cid": str(course_id), "sid": student_id},
        )
        return await _build_course_response(db, course)


@router.put("/courses/{course_id}/teacher", response_model=AdminCourseResponse)
async def change_course_teacher(course_id: UUID, payload: AdminTransferTeacherRequest):
    async with get_db_session() as db:
        course = await db.get(Course, course_id)
        if course is None:
            raise HTTPException(status_code=404, detail="Course not found.")
        teacher = await db.get(User, payload.teacher_id.strip())
        if teacher is None:
            raise HTTPException(status_code=404, detail="Teacher user not found.")
        course.created_by_id = teacher.id
        await db.flush()
        return await _build_course_response(db, course)
