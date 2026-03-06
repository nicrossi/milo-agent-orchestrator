from dotenv import load_dotenv
load_dotenv()

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from src.api.routers import chat
from src.core.database import init_db, close_db
from src.services.rag import IntegratedRAGService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("milo-orchestrator.main")

# Single application-wide RAG service instance shared across all requests.
rag_service = IntegratedRAGService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initialising database…")
    await init_db()
    logger.info("Database ready.")

    logger.info("Starting up — booting RAG process pool…")
    rag_service.start()
    logger.info("RAG service ready.")

    yield

    logger.info("Shutting down — closing RAG process pool…")
    rag_service.stop()
    logger.info("Shutting down — closing database…")
    await close_db()


app = FastAPI(title="Milo Orchestrator API", lifespan=lifespan)
app.include_router(chat.router)

@app.get("/healthcheck", tags=["System"])
def health_check():
    return {"status": "healthy"}
