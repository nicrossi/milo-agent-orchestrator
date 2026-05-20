-- Milo Orchestrator - Add photo_data_url to users
-- Safe to run multiple times (idempotent).

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS photo_data_url TEXT NULL;

COMMIT;
