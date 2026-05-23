import asyncio
import logging
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from typing import List

# NOTE: sentence_transformers pulls in torch (~200MB) + transformers and an
# embedding model (~50-500MB). Importing it at module load makes the main
# FastAPI process exceed Render's 512MB free-tier limit before it can bind a
# port. We defer the import to the worker initializer below, which only runs
# when IntegratedRAGService.start() is called — and start() is gated behind
# the RAG_ENABLED env var in main.py.
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("milo-orchestrator.rag")

# These variables only exist inside the isolated memory of the background
# worker process — never in the main FastAPI process.
_worker_model = None  # type: ignore[var-annotated]


def _init_worker_model(model_id: str) -> None:
    """Initializer called exactly ONCE per worker process at pool startup.
    The heavy sentence_transformers import lives here so it only loads in the
    forked worker process — never in the parent FastAPI process."""
    global _worker_model
    from sentence_transformers import SentenceTransformer  # lazy import
    _worker_model = SentenceTransformer(model_id)
    logging.info("Worker process initialized with model: %s", model_id)


def _generate_embedding_sync(query: str) -> List[float]:
    """Synchronous, CPU-bound function executed inside the worker process."""
    if _worker_model is None:
        raise RuntimeError("Worker model not initialized.")
    return _worker_model.encode(query).tolist()


class VectorDBRepository:
    """Handles strictly the SQL logic for retrieving similar vectors."""

    @staticmethod
    async def search_similar(
        db: AsyncSession,
        vector: List[float],
        limit: int,
        user_id: str | None = None,
        activity_id: str | None = None,
    ) -> List[str]:
        # The embedding vector is an internal List[float]
        # (never user input) so it is safe to inline it as
        # a literal string directly into the SQL expression.
        vector_literal = str(vector)
        # Activity-scoped retrieval takes precedence: when a student is
        # chatting inside an activity, every student on that activity should
        # see the same teacher-provided embeddings (joined on activity_id).
        #
        # TODO: the activity_id column was added late via
        # migration-document-embeddings-activity-id.sql; existing rows are
        # NULL, so this branch returns empty until embeddings are re-ingested
        # with activity_id set. A backfill job (assign each row's
        # activity_id from its source upload) is the proper follow-up.
        if activity_id:
            sql = text(f"""
                SELECT chunk_text
                FROM document_embeddings
                WHERE activity_id = :activity_id
                ORDER BY embedding <=> '{vector_literal}'::vector ASC
                LIMIT :limit
            """)
            result = await db.execute(
                sql, {"limit": limit, "activity_id": activity_id}
            )
        elif user_id:
            sql = text(f"""
                SELECT chunk_text
                FROM document_embeddings
                WHERE owner_user_id = :user_id
                   OR owner_user_id = 'GLOBAL'
                ORDER BY embedding <=> '{vector_literal}'::vector ASC
                LIMIT :limit
            """)
            result = await db.execute(sql, {"limit": limit, "user_id": user_id})
        else:
            sql = text(f"""
                SELECT chunk_text
                FROM document_embeddings
                ORDER BY embedding <=> '{vector_literal}'::vector ASC
                LIMIT :limit
            """)
            result = await db.execute(sql, {"limit": limit})
        return [row[0] for row in result.fetchall()]


class IntegratedRAGService:
    """Coordinates the Process Pool and the DB Repository asynchronously."""

    def __init__(
        self,
        model_id: str = "sentence-transformers/all-MiniLM-L6-v2",
        max_workers: int = 1,
    ) -> None:
        self.model_id = model_id
        self._max_workers = max_workers
        self._pool: ProcessPoolExecutor | None = None

    def start(self) -> None:
        """Boot the process pool. MUST be called during FastAPI lifespan startup."""

        # Force 'spawn' to prevent PyTorch CUDA / fork-memory deadlocks on Linux.
        mp_context = mp.get_context("spawn")
        self._pool = ProcessPoolExecutor(
            max_workers=self._max_workers,
            mp_context=mp_context,
            initializer=_init_worker_model,
            initargs=(self.model_id,),
        )
        logger.info("Integrated RAG Process Pool started with %d worker(s).",
                    self._max_workers)

    def stop(self) -> None:
        """Shut down the background worker processes"""
        if self._pool:
            self._pool.shutdown(wait=True)
            self._pool = None
            logger.info("Integrated RAG Process Pool shut down.")

    async def retrieve_context(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 3,
        user_id: str | None = None,
        activity_id: str | None = None,
    ) -> List[str]:
        """Main entry point called by the OrchestratorAgent. Returns an empty
        list when the embedding pool isn't running (e.g. when RAG_ENABLED=false
        on memory-constrained deploys). Callers must already tolerate an empty
        result — there can simply be no relevant embeddings for a given query."""
        if not self._pool:
            logger.debug("RAG pool not running — returning empty context.")
            return []

        loop = asyncio.get_running_loop()

        try:
            vector = await loop.run_in_executor(
                self._pool, _generate_embedding_sync, query
            )
        except Exception as e:
            logger.error("Embedding generation failed", exc_info=True)
            raise RuntimeError("Failed to generate vector embedding.") from e

        # Async pgvector DB retrieval
        try:
            return await VectorDBRepository.search_similar(
                db, vector, limit, user_id=user_id, activity_id=activity_id
            )
        except Exception as e:
            logger.error("Database vector search failed", exc_info=True)
            raise RuntimeError("Failed to retrieve documents from DB.") from e
