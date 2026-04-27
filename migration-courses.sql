-- Milo Orchestrator - Course + Enrollment + Activity Assignment migration
-- Safe to run multiple times (idempotent).

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS courses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT NULL,
    created_by_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_courses_created_by
        FOREIGN KEY (created_by_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS course_enrollments (
    course_id UUID NOT NULL,
    student_id VARCHAR(255) NOT NULL,
    added_by_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (course_id, student_id),
    CONSTRAINT fk_course_enrollments_course
        FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE,
    CONSTRAINT fk_course_enrollments_student
        FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_course_enrollments_added_by
        FOREIGN KEY (added_by_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS activity_course_assignments (
    activity_id UUID NOT NULL,
    course_id UUID NOT NULL,
    assigned_by_id VARCHAR(255) NOT NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (activity_id, course_id),
    CONSTRAINT fk_activity_course_assignments_activity
        FOREIGN KEY (activity_id) REFERENCES reflection_activities(id) ON DELETE CASCADE,
    CONSTRAINT fk_activity_course_assignments_course
        FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE,
    CONSTRAINT fk_activity_course_assignments_assigned_by
        FOREIGN KEY (assigned_by_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_courses_created_by_id
    ON courses (created_by_id);

CREATE INDEX IF NOT EXISTS ix_course_enrollments_student_id
    ON course_enrollments (student_id);

CREATE INDEX IF NOT EXISTS ix_activity_course_assignments_course_id
    ON activity_course_assignments (course_id);

CREATE INDEX IF NOT EXISTS ix_activity_course_assignments_assigned_by_id
    ON activity_course_assignments (assigned_by_id);

COMMIT;

