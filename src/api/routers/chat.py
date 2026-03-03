import logging

from fastapi import APIRouter, HTTPException, WebSocket

from src.orchestration.agent import OrchestratorAgent
from src.schemas.chat import ChatRequest, ChatResponse
from src.api.session import ChatSession

logger = logging.getLogger("milo-orchestrator.api")
router = APIRouter(prefix="/chat", tags=["Chat"])

# Maintain a single instance of the OrchestratorAgent for the lifetime
# of the application to reuse its internal RAG and LLM client connections.
try:
    agent = OrchestratorAgent()
except Exception as _agent_init_err:
    logger.warning(
        "CRITICAL: OrchestratorAgent failed to initialize at startup. "
        "Chat endpoints will reject requests until resolved. Error: %s",
        _agent_init_err,
        exc_info=True,
    )
    agent = None

_AGENT_OFFLINE_MSG = "The chat orchestration service is currently unavailable."


# ── Endpoints ─────────────────────────────────────────────────────────
@router.post("", response_model=ChatResponse)
async def process_stateless_chat(payload: ChatRequest):
    """Processes a single chat query (no session state)."""
    if agent is None:
        raise HTTPException(status_code=503, detail=_AGENT_OFFLINE_MSG)

    try:
        answer = await agent.process_query(payload.query)
        return ChatResponse(answer=answer)
    except RuntimeError as err:
        logger.error("Stateless chat failed: %s", err, exc_info=True)
        raise HTTPException(status_code=503, detail=str(err))
    except Exception as err:
        logger.error("Unexpected stateless chat error: %s", err, exc_info=True)
        raise HTTPException(status_code=500, detail="An internal server error occurred.")


@router.websocket("/ws/{session_id}")
async def process_stateful_websocket_chat(websocket: WebSocket, session_id: str):
    """Delegates the full WebSocket lifecycle to a `ChatSession` instance."""
    if agent is None:
        await websocket.accept()
        try:
            await websocket.send_json({"type": "error", "detail": _AGENT_OFFLINE_MSG})
            await websocket.close(code=1011, reason="Agent offline")
        except Exception:
            pass
        return

    session = ChatSession(websocket, session_id, agent)
    await session.run()
