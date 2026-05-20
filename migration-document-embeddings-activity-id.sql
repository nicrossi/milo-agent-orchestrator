-- Adds activity_id to document_embeddings to match the code in
-- src/services/rag.py (the activity-scoped retrieval branch queries
-- WHERE activity_id = :activity_id).
--
-- Apply with:
--   psql "$DATABASE_URL" -f migration-document-embeddings-activity-id.sql
--
-- Existing rows are left with activity_id = NULL. They become invisible to
-- the activity-scoped branch but remain visible to the user-scoped branch
-- via owner_user_id. Backfill is a follow-up: when a re-ingest of teacher
-- documents tags each embedding with its source activity, the activity
-- branch becomes useful again.

ALTER TABLE document_embeddings
    ADD COLUMN IF NOT EXISTS activity_id UUID;

CREATE INDEX IF NOT EXISTS idx_document_embeddings_activity_id
    ON document_embeddings (activity_id);
