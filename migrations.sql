-- Milo Orchestrator — Consolidated DB schema
--
-- Single-file, idempotent mirror of the live `milo_dev` schema. Every statement
-- uses IF NOT EXISTS / ON CONFLICT guards, so re-running this file on a database
-- that already has any subset applied is a safe no-op.
--
-- This file is the single source of truth for a fresh install. It reproduces all
-- 16 tables that exist at runtime, including the ones the backend would otherwise
-- create via SQLAlchemy `Base.metadata.create_all` (src/core/database.py) and the
-- ones the sibling `milo-ingest` service writes to (document_embeddings).
--
--
-- Audiences and how to use this file
--
--   * Fresh install (new dev / new env):
--       1. Create an empty Postgres database.
--       2. Run this file:    psql -d <db> -f migrations.sql
--       3. Boot the backend. src/core/database.py runs create_all on startup,
--          which is a no-op because every table already exists.
--
--   * Existing dev catching up:
--       1. Pull the latest code.
--       2. Run this file (idempotent).
--       3. Restart the backend.
--
--
-- Table inventory (16) — created in FK-dependency order below:
--   Base (no FK):  users, roles, schools
--   Children:      user_roles, school_memberships, reflection_activities,
--                  courses, course_enrollments, activity_course_assignments,
--                  activity_files, chat_sessions, chat_messages,
--                  chat_session_ownership, session_metrics, notifications,
--                  document_embeddings
--
-- One-off operational scripts (NOT included here, ship separately):
--   * scripts/migrate_legacy_session_ids_to_uuid.sql — converts old varchar
--     session_id columns to uuid; relevant only for pre-UUID installations.
--
-- NOTE: live `users.id` is varchar(128). The ORM (src/core/models.py) declares
-- String(255); this file mirrors the live DB (128). FK columns that reference
-- users elsewhere are varchar(255) — Postgres allows the length difference.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Transaction 1 — core schema (no pgvector dependency).
-- Kept separate from the pgvector block below so that, on a server where the
-- `vector` extension is unavailable, the 15 non-vector tables (incl.
-- reflection_activities) still get created instead of the whole file rolling
-- back. document_embeddings + its vector extension live in Transaction 2.
-- ----------------------------------------------------------------------------
BEGIN;

-- UUID generation for gen_random_uuid() defaults.
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ============================================================================
-- Base tables (no foreign keys)
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
  id             VARCHAR(128) PRIMARY KEY,                 -- Firebase UID
  email          VARCHAR(255),
  display_name   VARCHAR(255),
  role           VARCHAR(50)  NOT NULL DEFAULT 'student',
  photo_data_url TEXT
);

CREATE TABLE IF NOT EXISTS roles (
  id          SMALLSERIAL PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE,
  description TEXT
);

CREATE TABLE IF NOT EXISTS schools (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- Child tables (FK-ordered)
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_roles (
  user_id     TEXT     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id     SMALLINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS school_memberships (
  school_id  UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_scope TEXT,                          -- optional: school_admin, teacher, student
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (school_id, user_id)
);

CREATE TABLE IF NOT EXISTS reflection_activities (
  id                        UUID         PRIMARY KEY,
  title                     VARCHAR(255) NOT NULL,
  teacher_goal              TEXT         NOT NULL,
  context_description       TEXT         NOT NULL,
  status                    VARCHAR(50)  NOT NULL,
  created_by_id             VARCHAR(255) NOT NULL REFERENCES users(id),
  deadline                  TIMESTAMPTZ,
  deadline_reminder_sent_at TIMESTAMPTZ,
  deadline_summary_sent_at  TIMESTAMPTZ,
  created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS courses (
  id            UUID         PRIMARY KEY,
  name          VARCHAR(255) NOT NULL,
  description   TEXT,
  created_by_id VARCHAR(255) NOT NULL REFERENCES users(id),
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS course_enrollments (
  course_id  UUID         NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  student_id VARCHAR(255) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  added_by_id VARCHAR(255) NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  PRIMARY KEY (course_id, student_id)
);

CREATE TABLE IF NOT EXISTS activity_course_assignments (
  activity_id    UUID         NOT NULL REFERENCES reflection_activities(id) ON DELETE CASCADE,
  course_id      UUID         NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  assigned_by_id VARCHAR(255) NOT NULL REFERENCES users(id),
  assigned_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  PRIMARY KEY (activity_id, course_id)
);

CREATE TABLE IF NOT EXISTS activity_files (
  id             UUID          PRIMARY KEY,
  activity_id    UUID          NOT NULL REFERENCES reflection_activities(id) ON DELETE CASCADE,
  uploaded_by_id VARCHAR(255)  NOT NULL REFERENCES users(id),
  filename       VARCHAR(500)  NOT NULL,
  s3_key         VARCHAR(1000) NOT NULL UNIQUE,
  content_type   VARCHAR(255)  NOT NULL,
  size_bytes     BIGINT        NOT NULL,
  status         VARCHAR(20)   NOT NULL,
  created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  confirmed_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id           UUID         PRIMARY KEY,
  activity_id  UUID         NOT NULL REFERENCES reflection_activities(id),
  student_id   VARCHAR(255) NOT NULL REFERENCES users(id),
  status       VARCHAR(50)  NOT NULL,
  started_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  transcript   TEXT         NOT NULL DEFAULT '',
  policy_state JSONB,
  finalized_at TIMESTAMPTZ
);

-- Orchestrator-native chat persistence (used by /chat/ws + /chat/history).
-- No FK on session_id in live DB (history spans multiple chat_sessions rows).
CREATE TABLE IF NOT EXISTS chat_messages (
  id         UUID         PRIMARY KEY,
  session_id UUID         NOT NULL,
  user_id    VARCHAR(255) NOT NULL,
  role       VARCHAR(20)  NOT NULL,         -- expected values: user | model
  content    TEXT         NOT NULL,
  created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_session_ownership (
  session_id VARCHAR(255) PRIMARY KEY,
  user_id    VARCHAR(255) NOT NULL,
  created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS session_metrics (
  session_id                       UUID PRIMARY KEY REFERENCES chat_sessions(id),
  reflection_quality_level         VARCHAR(50),
  reflection_quality_justification TEXT,
  reflection_quality_evidence      JSON,
  reflection_quality_action        TEXT,
  calibration_level                VARCHAR(50),
  calibration_justification        TEXT,
  calibration_evidence             JSON,
  calibration_action               TEXT,
  contextual_transfer_level        VARCHAR(50),
  contextual_transfer_justification TEXT,
  contextual_transfer_evidence     JSON,
  contextual_transfer_action       TEXT,
  policy_metrics                   JSONB,
  confidence_score                 SMALLINT,
  confidence_justification         TEXT,
  confidence_evidence              JSONB
);

CREATE TABLE IF NOT EXISTS notifications (
  id          UUID         PRIMARY KEY,
  user_id     VARCHAR(255) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        VARCHAR(50)  NOT NULL,
  activity_id UUID         REFERENCES reflection_activities(id) ON DELETE CASCADE,
  title       VARCHAR(255) NOT NULL,
  body        TEXT,
  deep_link   TEXT         NOT NULL,
  read_at     TIMESTAMPTZ,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- (document_embeddings lives in Transaction 2 — it needs the vector extension.)


-- ============================================================================
-- Indexes (matches live milo_dev exactly)
-- ============================================================================

CREATE INDEX IF NOT EXISTS ix_chat_messages_session_created
  ON chat_messages (session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_chat_messages_user_session_created
  ON chat_messages (user_id, session_id, created_at);

CREATE INDEX IF NOT EXISTS ix_chat_session_ownership_user_id
  ON chat_session_ownership (user_id);

CREATE INDEX IF NOT EXISTS ix_chat_sessions_finalized_at
  ON chat_sessions (finalized_at);

CREATE INDEX IF NOT EXISTS ix_courses_created_by_id
  ON courses (created_by_id);

CREATE INDEX IF NOT EXISTS ix_course_enrollments_student_id
  ON course_enrollments (student_id);

CREATE INDEX IF NOT EXISTS ix_activity_course_assignments_course_id
  ON activity_course_assignments (course_id);
CREATE INDEX IF NOT EXISTS ix_activity_course_assignments_assigned_by_id
  ON activity_course_assignments (assigned_by_id);

CREATE INDEX IF NOT EXISTS ix_activity_files_activity_id
  ON activity_files (activity_id);

CREATE INDEX IF NOT EXISTS ix_reflection_activities_deadline
  ON reflection_activities (deadline);

CREATE INDEX IF NOT EXISTS ix_notifications_user_created
  ON notifications (user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_notifications_user_unread
  ON notifications (user_id, created_at DESC)
  WHERE read_at IS NULL;


-- ============================================================================
-- Seeds
-- ============================================================================

-- Base roles (safe, no duplicates)
INSERT INTO roles (code, description) VALUES
  ('milo_admin', 'Global Milo administrator'),
  ('school_admin', 'School administrator'),
  ('teacher', 'Teacher'),
  ('student', 'Student')
ON CONFLICT (code) DO NOTHING;

-- Shared GLOBAL principal for platform-wide context documents
INSERT INTO users (id, email, display_name)
VALUES ('GLOBAL', 'global@milo.local', 'Milo Global Context')
ON CONFLICT (id) DO NOTHING;

COMMIT;


-- ============================================================================
-- Transaction 2 — pgvector storage (milo-ingest writes here).
-- Isolated so a server without the `vector` extension still gets the core
-- schema from Transaction 1. If this block fails, install pgvector and re-run
-- the file (idempotent).
-- ============================================================================
BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- No FK in live DB.
CREATE TABLE IF NOT EXISTS document_embeddings (
  id            SERIAL PRIMARY KEY,
  source_file   TEXT NOT NULL,
  chunk_index   INT  NOT NULL,
  chunk_text    TEXT NOT NULL,
  embedding     vector(384),
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  owner_user_id VARCHAR(255),
  activity_id   UUID
);

CREATE INDEX IF NOT EXISTS idx_source_file
  ON document_embeddings (source_file);
CREATE INDEX IF NOT EXISTS ix_document_embeddings_activity_id
  ON document_embeddings (activity_id);
CREATE INDEX IF NOT EXISTS idx_embedding
  ON document_embeddings USING hnsw (embedding vector_cosine_ops);

COMMIT;
