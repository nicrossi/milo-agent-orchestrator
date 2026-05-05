-- Milo Orchestrator — Consolidated DB migrations
--
-- Single-file migration covering the full SQL schema delta. Idempotent: every
-- statement uses IF NOT EXISTS / IF EXISTS guards, so re-running this file on
-- a database that already has any subset applied is a safe no-op.
--
--
-- Audiences and how to use this file
--
--   * Fresh install (new dev / new env):
--       1. Create an empty Postgres database.
--       2. Boot the backend once. src/core/database.py runs
--          Base.metadata.create_all + a few ALTER TABLE IF NOT EXISTS on
--          startup, which creates the runtime-managed tables
--          (users, reflection_activities, chat_sessions, chat_messages,
--          session_metrics, courses, course_enrollments,
--          activity_course_assignments, notifications). Doing this first
--          satisfies the foreign-key references this file makes to
--          reflection_activities below.
--       3. Run this file:    psql -d <db> -f migrations.sql
--          Adds the vestigial tables from earlier schema (roles, schools,
--          conversations, etc) plus column-level additions that
--          create_all does not handle (deadline columns on
--          reflection_activities, finalized_at on chat_sessions).
--       4. Restart the backend.
--
--   * Existing dev catching up to a newer schema:
--       1. Pull the latest code.
--       2. Run this file.
--       3. Restart the backend.
--
--
-- Section index (run order is preserved by the position in this file):
--
--   Section 1 — Bootstrap schema (users, roles, schools, conversations,
--               messages, chat_messages, chat_session_ownership, documents,
--               document_embeddings + base indexes + role/global seeds).
--   Section 2 — Courses, course enrollments, activity-course assignments.
--   Section 3 — reflection_activities deadline support
--               (deadline, deadline_reminder_sent_at) + index.
--   Section 4 — chat_sessions.finalized_at + notifications table + indexes.
--   Section 5 — reflection_activities.deadline_summary_sent_at +
--               drop deprecated all_completed_notified_at column.
--
-- One-off operational scripts (NOT included here, ship separately):
--   * scripts/migrate_legacy_session_ids_to_uuid.sql — converts old
--     varchar session_id columns to uuid; relevant only for installations
--     that ran milo before the UUID switchover.
--
-- ============================================================================


-- ============================================================================
-- Section 1 — Bootstrap schema
-- ============================================================================
BEGIN;

-- Needed for UUID generation and vectors (if not already present)
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- 1) Users + Roles
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,                    -- Firebase UID
  email TEXT UNIQUE NOT NULL,
  display_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS roles (
  id SMALLSERIAL PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  description TEXT
);

CREATE TABLE IF NOT EXISTS user_roles (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id SMALLINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, role_id)
);

-- 2) Schools + Memberships
CREATE TABLE IF NOT EXISTS schools (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS school_memberships (
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_scope TEXT,                        -- optional: school_admin, teacher, student
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (school_id, user_id)
);

-- 3) Conversations + Messages
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  school_id UUID REFERENCES schools(id) ON DELETE SET NULL,
  title TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  sender_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  sender_type TEXT NOT NULL CHECK (sender_type IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3.b) Orchestrator-native chat persistence (used by /chat/ws + /chat/history).
-- NOTE: session_id is declared VARCHAR here for backwards compatibility with
-- pre-UUID installations. The current models expect UUID; the runtime
-- auto-migration in src/core/database.py creates this table fresh with UUID
-- on first boot. For existing varchar installs, run
-- scripts/migrate_legacy_session_ids_to_uuid.sql to convert.
CREATE TABLE IF NOT EXISTS chat_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id VARCHAR(255) NOT NULL,
  user_id VARCHAR(255) NOT NULL,
  role VARCHAR(20) NOT NULL,              -- expected values: user | model
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_session_ownership (
  session_id VARCHAR(255) PRIMARY KEY,
  user_id VARCHAR(255) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- If chat_messages already exists from an older version, ensure required columns exist
ALTER TABLE chat_messages
  ADD COLUMN IF NOT EXISTS session_id VARCHAR(255),
  ADD COLUMN IF NOT EXISTS user_id VARCHAR(255),
  ADD COLUMN IF NOT EXISTS role VARCHAR(20),
  ADD COLUMN IF NOT EXISTS content TEXT,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- 4) Documents (ownership/context metadata for RAG)
CREATE TABLE IF NOT EXISTS documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  school_id UUID REFERENCES schools(id) ON DELETE SET NULL,
  conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  source_file TEXT NOT NULL,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5) Vector storage table (ingest writes here). Create if missing, then extend safely.
CREATE TABLE IF NOT EXISTS document_embeddings (
  id SERIAL PRIMARY KEY,
  source_file TEXT NOT NULL,
  chunk_index INT NOT NULL,
  chunk_text TEXT NOT NULL,
  embedding vector(384),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5.b) Link/extend existing document_embeddings table (without dropping anything)
ALTER TABLE document_embeddings
  ADD COLUMN IF NOT EXISTS document_id UUID,
  ADD COLUMN IF NOT EXISTS owner_user_id TEXT,
  ADD COLUMN IF NOT EXISTS school_id UUID,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

-- Add FK constraints only if missing
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_chat_session_ownership_user_id'
  ) THEN
    ALTER TABLE chat_session_ownership
      ADD CONSTRAINT fk_chat_session_ownership_user_id
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_document_embeddings_document_id'
  ) THEN
    ALTER TABLE document_embeddings
      ADD CONSTRAINT fk_document_embeddings_document_id
      FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE SET NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_document_embeddings_owner_user_id'
  ) THEN
    ALTER TABLE document_embeddings
      ADD CONSTRAINT fk_document_embeddings_owner_user_id
      FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_document_embeddings_school_id'
  ) THEN
    ALTER TABLE document_embeddings
      ADD CONSTRAINT fk_document_embeddings_school_id
      FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE SET NULL;
  END IF;
END $$;

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_user_roles_user_id ON user_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_school_memberships_user_id ON school_memberships(user_id);

CREATE INDEX IF NOT EXISTS idx_conversations_owner_user_id ON conversations(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_school_id ON conversations(school_id);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_sender_user_id ON messages(sender_user_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);

CREATE INDEX IF NOT EXISTS ix_chat_messages_session_created ON chat_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_chat_messages_user_session_created ON chat_messages(user_id, session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_chat_session_ownership_user_id ON chat_session_ownership(user_id);

CREATE INDEX IF NOT EXISTS idx_documents_owner_user_id ON documents(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_documents_school_id ON documents(school_id);
CREATE INDEX IF NOT EXISTS idx_documents_conversation_id ON documents(conversation_id);

CREATE INDEX IF NOT EXISTS idx_document_embeddings_document_id ON document_embeddings(document_id);
CREATE INDEX IF NOT EXISTS idx_document_embeddings_owner_user_id ON document_embeddings(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_document_embeddings_school_id ON document_embeddings(school_id);
CREATE INDEX IF NOT EXISTS idx_embedding ON document_embeddings USING hnsw (embedding vector_cosine_ops);

-- Seed base roles (safe, no duplicates)
INSERT INTO roles (code, description) VALUES
  ('milo_admin', 'Global Milo administrator'),
  ('school_admin', 'School administrator'),
  ('teacher', 'Teacher'),
  ('student', 'Student')
ON CONFLICT (code) DO NOTHING;

-- Seed shared GLOBAL principal for platform-wide context documents
INSERT INTO users (id, email, display_name)
VALUES ('GLOBAL', 'global@milo.local', 'Milo Global Context')
ON CONFLICT (id) DO NOTHING;

COMMIT;


-- ============================================================================
-- Section 2 — Courses, enrollments, activity-course assignments
-- ============================================================================
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


-- ============================================================================
-- Section 3 — reflection_activities deadline support
-- ============================================================================
-- Adds deadline fields used by the activity creation flow + the deadline
-- reminder worker. Kept nullable in DB for backwards compatibility with rows
-- created before these columns existed; the API requires deadline on creation.
BEGIN;

ALTER TABLE reflection_activities
  ADD COLUMN IF NOT EXISTS deadline                  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS deadline_reminder_sent_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_reflection_activities_deadline
  ON reflection_activities (deadline);

COMMIT;


-- ============================================================================
-- Section 4 — chat_sessions.finalized_at + in-app notifications
-- ============================================================================
-- finalized_at is set by the orchestrator when the LLM judges the reflection
-- has reached natural closure. Decoupled from session.status (owned by the
-- metrics-evaluation lifecycle). Resume logic and downstream deadline-summary
-- triggers key off finalized_at, not status=EVALUATED.
--
-- notifications backs the in-app notification bell. Created server-side; the
-- frontend polls GET /me/notifications on the home page.
BEGIN;

ALTER TABLE chat_sessions
  ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_chat_sessions_finalized_at
  ON chat_sessions (finalized_at);

CREATE TABLE IF NOT EXISTS notifications (
  id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      VARCHAR(255) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type         VARCHAR(50)  NOT NULL,
  activity_id  UUID         NULL REFERENCES reflection_activities(id) ON DELETE CASCADE,
  title        VARCHAR(255) NOT NULL,
  body         TEXT         NULL,
  deep_link    TEXT         NOT NULL,
  read_at      TIMESTAMPTZ  NULL,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_notifications_user_created
  ON notifications (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_notifications_user_unread
  ON notifications (user_id, created_at DESC)
  WHERE read_at IS NULL;

COMMIT;


-- ============================================================================
-- Section 5 — Teacher deadline-summary trigger
-- ============================================================================
-- Replaces the older "all-students-completed" trigger with a deadline-summary
-- one. deadline_summary_sent_at is the idempotency marker for the teacher's
-- summary email + bell, sent once when the activity's deadline elapses.
-- all_completed_notified_at is dropped because the corresponding trigger has
-- been removed.
BEGIN;

ALTER TABLE reflection_activities
  ADD COLUMN IF NOT EXISTS deadline_summary_sent_at TIMESTAMPTZ;

ALTER TABLE reflection_activities
  DROP COLUMN IF EXISTS all_completed_notified_at;

COMMIT;
