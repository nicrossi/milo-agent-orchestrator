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
            await conn.run_sync(Base.metadata.create_all)
            # Lightweight idempotent migrations for existing deployments.
            await conn.execute(
                text("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS user_id VARCHAR(255)")
            )
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
            await conn.execute(
                text("ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR(255)")
            )
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
