from dotenv import load_dotenv
load_dotenv()

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from src.api.routers import chat
from src.core.database import init_db, close_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("milo-orchestrator.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initialising database…")
    await init_db()
    logger.info("Database ready.")
    yield
    logger.info("Shutting down — closing database…")
    await close_db()


app = FastAPI(title="Milo Orchestrator API", lifespan=lifespan)
app.include_router(chat.router)

@app.get("/healthcheck", tags=["System"])
def health_check():
    return {"status": "healthy"}
