# Milo Agent Orchestrator

Proof-of-concept API server that exposes a Retrieval-Augmented Generation (RAG) chat agent over HTTP and WebSocket.

**Status:** Working PoC.

### Key design decisions

- The WebSocket router is a thin delegate. All connection state lives in `ChatSession` (`src/api/session.py`).
- `OrchestratorAgent` (`src/orchestration/agent.py`) is decomposed into named pipeline stages — no nested exception handling.
- Conversation history is persisted per session in PostgreSQL so context survives reconnects.
- Partial model responses are saved on client disconnect to prevent orphaned user turns.


## Stack

| Layer       | Technology                          |
|-------------|-------------------------------------|
| Framework   | FastAPI 0.135                       |
| Database    | PostgreSQL via SQLAlchemy (asyncpg) |
| LLM         | Google Gemini (google-genai)        |
| RAG         | External HTTP service (httpx)       |
| Runtime     | Python 3.11+, uvicorn               |


## Endpoints

| Method    | Path                    | Description                              |
|-----------|-------------------------|------------------------------------------|
| GET       | `/healthcheck`          | Liveness probe                           |
| POST      | `/chat`                 | Stateless single-turn query              |
| WebSocket | `/chat/ws/{session_id}` | Stateful streaming chat with persistence |

### WebSocket protocol

Messages are JSON frames with a `type` discriminator:

**Server to client:**

```json
{"type": "chunk", "text": "..."}
{"type": "done"}
{"type": "error", "detail": "..."}
```

**Client to server:** plain text string (the user message).

The server closes idle connections after 1 hour (code `1008`).


## Environment variables

| Variable         | Required | Description            |
|------------------|----------|------------------------|
| `DATABASE_URL`   | Yes      | PostgreSQL connection string |
| `GOOGLE_API_KEY` | Yes      | Google Gemini API key  |
| `RAG_SERVICE_URL`| Yes      | RAG service base URL |
| `LLM_MODEL`      | No       | Gemini model name (default: `gemini-2.5-flash`) |
| `DB_POOL_SIZE`   | No       | SQLAlchemy pool size (default: `5`) |
| `DB_MAX_OVERFLOW` | No       | SQLAlchemy max overflow (default: `10`) |


## Running locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set required environment variables (or use a .env file)
export DATABASE_URL="postgresql://user:pass@localhost:5432/milo"
export GOOGLE_API_KEY="your-key"
export RAG_SERVICE_URL="http://rag-service-url"

# Start the server
uvicorn src.main:app --port 3000
```

Tables are created automatically on startup via the FastAPI lifespan hook.


## Project layout

```
src/
  main.py                    Application entry point and lifespan management
  api/
  |- routers/chat.py          HTTP and WebSocket endpoint definitions
  |- session.py               ChatSession — WebSocket lifecycle encapsulation
  orchestration/
  |- agent.py                 OrchestratorAgent — RAG + LLM pipeline
  adapters/
  |- clients/
  |  |- chat_history.py        Database repository for conversation turns
  |  |- rag.py                 HTTP client for the external RAG service
  |- llm/
  |  |- base.py                Abstract LLM adapter interface
  |  |- gemini.py              Google Gemini implementation
  core/
  |- database.py              Async engine, session factory, init/close
  |- models.py                SQLAlchemy ORM models (ChatMessage)
  schemas/
  |- chat.py                  Pydantic request/response models
```

