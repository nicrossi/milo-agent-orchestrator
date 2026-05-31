# Milo orchestrator — FastAPI + WebSocket + RAG (SentenceTransformer) image.
# Built for a single-instance DigitalOcean Droplet behind Caddy.
FROM python:3.11-slim

# All pinned deps (torch CPU, sentence-transformers, asyncpg, pydantic) ship manylinux
# wheels, so no compiler/apt packages are needed. migrations.sql is applied via a
# separate postgres container, not from inside this image.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf-cache

WORKDIR /app

# Install CPU-only torch FIRST so the default-index resolve in requirements.txt
# (torch==2.10.0) is already satisfied and never drags in ~2GB of CUDA wheels.
RUN pip install --no-cache-dir torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image so the first request doesn't block on a
# download and offline workers come up instantly.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY . .

EXPOSE 8000

# Single worker process: the metrics queue + RAG ProcessPool are in-process state.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
