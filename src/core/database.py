import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from src.core.models import Base

logger = logging.getLogger("milo-orchestrator.database")

# Lazy globals — initialised by init_db() during the FastAPI lifespan.
engine: Optional[AsyncEngine] = None
async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _ensure_async_url(url: str | None) -> str:
    """Guarantee the URL uses the asyncpg driver."""
    if not url:
        raise ValueError("DATABASE_URL environment variable is required")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@asynccontextmanager  # type: ignore[misc]
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Provide a transactional database session."""
    if async_session_factory is None:
        raise RuntimeError("Database not initialised — call init_db() first")

    session = async_session_factory()
    try:
        yield session
        await session.commit()

    except asyncio.CancelledError:
        logger.debug("DB session cancelled mid-transaction — rolling back.")
        await session.rollback()
        raise
    except Exception:
        logger.error("DB session error — rolling back:", exc_info=True)
        await session.rollback()
        raise

    finally:
        await session.close()


async def init_db() -> None:
    """Create the engine, session factory, and all tables."""
    global engine, async_session_factory

    db_url = _ensure_async_url(os.getenv("DATABASE_URL"))

    pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
    max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))

    engine = create_async_engine(
        db_url,
        echo=False,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )

    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

            await conn.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS users (
                        id VARCHAR(128) PRIMARY KEY,
                        email VARCHAR(255),
                        display_name VARCHAR(255),
                        role VARCHAR(50) NOT NULL DEFAULT 'student'
                    )
                """)
            )

            await conn.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS reflection_activities (
                        id UUID PRIMARY KEY,
                        title VARCHAR(255) NOT NULL,
                        teacher_goal TEXT NOT NULL,
                        context_description TEXT NOT NULL,
                        status VARCHAR(50) NOT NULL,
                        created_by_id VARCHAR(255) NOT NULL
                    )
                """)
            )

            await conn.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        id UUID PRIMARY KEY,
                        activity_id UUID NOT NULL,
                        student_id VARCHAR(255) NOT NULL,
                        status VARCHAR(50) NOT NULL,
                        started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                        transcript TEXT NOT NULL DEFAULT ''
                    )
                """)
            )

            await conn.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id UUID PRIMARY KEY,
                        session_id UUID NOT NULL,
                        user_id VARCHAR(255) NOT NULL,
                        role VARCHAR(20) NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
                    )
                """)
            )

            await conn.execute(
                text("""
                     CREATE TABLE IF NOT EXISTS session_metrics (
                         session_id UUID PRIMARY KEY,
                         reflection_quality_level VARCHAR(50),
                         reflection_quality_justification TEXT,
                         reflection_quality_evidence JSON,
                         reflection_quality_action TEXT,
                         calibration_level VARCHAR(50),
                         calibration_justification TEXT,
                         calibration_evidence JSON,
                         calibration_action TEXT,
                         contextual_transfer_level VARCHAR(50),
                         contextual_transfer_justification TEXT,
                         contextual_transfer_evidence JSON,
                         contextual_transfer_action TEXT
                     )
                     """)
            )

            # Lightweight idempotent migrations for existing deployments.
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) NOT NULL DEFAULT 'student'")
            )
            await conn.execute(
                text("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS user_id VARCHAR(255)")
            )
            await conn.execute(
                text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS transcript TEXT NOT NULL DEFAULT ''")
            )
            
            await conn.execute(
                text("ALTER TABLE session_metrics ALTER COLUMN reflection_quality_level TYPE VARCHAR(50)")
            )
            await conn.execute(
                text("ALTER TABLE session_metrics ALTER COLUMN calibration_level TYPE VARCHAR(50)")
            )
            await conn.execute(
                text("ALTER TABLE session_metrics ALTER COLUMN contextual_transfer_level TYPE VARCHAR(50)")
            )

            # Phase 5: resumable policy state + per-session policy metrics.
            await conn.execute(
                text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS policy_state JSONB")
            )
            await conn.execute(text("SAVEPOINT sp_alter_session_metrics"))
            try:
                await conn.execute(
                    text("ALTER TABLE session_metrics ADD COLUMN IF NOT EXISTS policy_metrics JSONB")
                )
            except Exception:
                # session_metrics may not exist yet on first boot; create_all
                # below will create it with the column included.
                await conn.execute(text("ROLLBACK TO SAVEPOINT sp_alter_session_metrics"))

            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chat_messages_user_session_created "
                    "ON chat_messages(user_id, session_id, created_at)"
                )
            )

            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS chat_session_ownership ("
                    " session_id VARCHAR(255) PRIMARY KEY,"
                    " user_id VARCHAR(255) NOT NULL,"
                    " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
                    ")"
                )
            )

            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chat_session_ownership_user_id "
                    "ON chat_session_ownership(user_id)"
                )
            )

            # Optional ownership metadata for user-scoped RAG retrieval.
            # document_embeddings is owned by milo-ingest; skip gracefully if absent.
            await conn.execute(text("SAVEPOINT sp_alter_embeddings"))
            try:
                await conn.execute(
                    text("ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR(255)")
                )
            except Exception:
                await conn.execute(text("ROLLBACK TO SAVEPOINT sp_alter_embeddings"))

            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")
    except Exception:
        logger.critical("Failed to create database tables", exc_info=True)
        raise


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    global engine, async_session_factory
    if engine:
        await engine.dispose()
        engine = None
        async_session_factory = None
    logger.info("Database engine disposed")
