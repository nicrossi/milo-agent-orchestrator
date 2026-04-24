BEGIN;

-- Preserve legacy pre-UUID session data before converting the active schema.
CREATE TABLE IF NOT EXISTS chat_messages_legacy (
    LIKE chat_messages INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS chat_session_ownership_legacy (
    LIKE chat_session_ownership INCLUDING ALL
);

INSERT INTO chat_messages_legacy
SELECT *
FROM chat_messages
WHERE session_id::text !~ '^[0-9a-fA-F-]{36}$'
ON CONFLICT (id) DO NOTHING;

DELETE FROM chat_messages
WHERE session_id::text !~ '^[0-9a-fA-F-]{36}$';

INSERT INTO chat_session_ownership_legacy
SELECT *
FROM chat_session_ownership
WHERE session_id::text !~ '^[0-9a-fA-F-]{36}$'
ON CONFLICT (session_id) DO NOTHING;

DELETE FROM chat_session_ownership
WHERE session_id::text !~ '^[0-9a-fA-F-]{36}$';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'chat_messages'
          AND column_name = 'session_id'
          AND udt_name = 'varchar'
    ) THEN
        ALTER TABLE chat_messages
        ALTER COLUMN session_id TYPE uuid
        USING session_id::uuid;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'chat_session_ownership'
          AND column_name = 'session_id'
          AND udt_name = 'varchar'
    ) THEN
        ALTER TABLE chat_session_ownership
        ALTER COLUMN session_id TYPE uuid
        USING session_id::uuid;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chat_messages_session_id_fkey'
    ) THEN
        ALTER TABLE chat_messages
        ADD CONSTRAINT chat_messages_session_id_fkey
        FOREIGN KEY (session_id) REFERENCES chat_sessions(id);
    END IF;
END $$;

COMMIT;
