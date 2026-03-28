from dotenv import load_dotenv
load_dotenv()

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routers import chat, activities
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

allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(activities.router)

@app.get("/healthcheck", tags=["System"])
def health_check():
    return {"status": "healthy"}
