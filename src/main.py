import os, sys
print(f"BOOT[1/6]: python entered main.py; PORT={os.environ.get('PORT')!r}; RAG_ENABLED={os.environ.get('RAG_ENABLED')!r}", flush=True)

from dotenv import load_dotenv
load_dotenv()
print("BOOT[2/6]: dotenv loaded", flush=True)

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
print("BOOT[3/6]: fastapi imported", flush=True)

from src.api.routers import activities; print("BOOT[4a/6]: activities", flush=True)
from src.api.routers import admin;      print("BOOT[4b/6]: admin", flush=True)
from src.api.routers import audio;      print("BOOT[4c/6]: audio", flush=True)
from src.api.routers import chat;       print("BOOT[4d/6]: chat", flush=True)
from src.api.routers import courses;    print("BOOT[4e/6]: courses", flush=True)
from src.api.routers import me;         print("BOOT[4f/6]: me", flush=True)
from src.api.routers import policy;     print("BOOT[4g/6]: policy", flush=True)
from src.api.routers import students;   print("BOOT[4h/6]: students", flush=True)
print("BOOT[4/6]: all routers imported", flush=True)

from src.core.database import init_db, close_db
from src.services.rag import IntegratedRAGService
from src.services.metrics_evaluator import start_worker, stop_worker
from src.services.deadline_reminders import (
    start_reminder_worker,
    stop_reminder_worker,
)
print("BOOT[5/6]: services imported (incl rag module)", flush=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("milo-orchestrator.main")

# RAG is gated by env var so memory-constrained deploys (e.g. Render free tier
# at 512MB) can skip booting the embedding model + ProcessPoolExecutor entirely.
# When disabled, retrieve_context() returns [] — chat still works, just without
# document-grounded context. Defaults to true to preserve existing dev behavior.
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").lower() == "true"

# Single application-wide RAG service instance shared across all requests.
rag_service = IntegratedRAGService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initialising database…")
    await init_db()
    logger.info("Database ready.")

    if RAG_ENABLED:
        logger.info("Starting up — booting RAG process pool…")
        rag_service.start()
        logger.info("RAG service ready.")
    else:
        logger.info("Starting up — RAG disabled (RAG_ENABLED=false). Skipping embedding pool.")

    logger.info("Starting up — background evaluation worker…")
    await start_worker()

    logger.info("Starting up — deadline reminder worker…")
    await start_reminder_worker()

    yield

    logger.info("Shutting down — deadline reminder worker…")
    await stop_reminder_worker()

    logger.info("Shutting down — background evaluation worker…")
    await stop_worker()

    if RAG_ENABLED:
        logger.info("Shutting down — closing RAG process pool…")
        rag_service.stop()
    logger.info("Shutting down — closing database…")
    await close_db()


print("BOOT[6/6]: constructing FastAPI app", flush=True)
app = FastAPI(title="Milo Orchestrator API", lifespan=lifespan)
print("BOOT[6/6]: FastAPI app constructed — uvicorn should now bind to PORT", flush=True)

allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]
# Additional comma-separated origins from env — for Vercel / custom domains.
# Example: ALLOWED_ORIGINS=https://milo.vercel.app,https://milo-foo.vercel.app
extra_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
if extra_origins:
    allowed_origins.extend([o.strip() for o in extra_origins.split(",") if o.strip()])
# Optional: match all Vercel preview deploys for a project via regex.
# Set ALLOWED_ORIGIN_REGEX=https://your-project-.*\.vercel\.app
allow_origin_regex = os.getenv("ALLOWED_ORIGIN_REGEX") or None

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(activities.router)
app.include_router(courses.router)
app.include_router(students.router)
app.include_router(me.router)
app.include_router(admin.router)
app.include_router(policy.router)
app.include_router(audio.router)

@app.get("/healthcheck", tags=["System"])
def health_check():
    return {"status": "healthy"}
