import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from src.core.database import get_db_session

from src.api.session import ChatSession
from src.core.auth import AuthenticatedUser, require_http_user, require_ws_user
from src.orchestration.agent import OrchestratorAgent
from src.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger("milo-orchestrator.api")
router = APIRouter(prefix="/chat", tags=["Chat"])

_agent: Optional[OrchestratorAgent] = None
_AGENT_OFFLINE_MSG = "The chat orchestration service is currently unavailable."


def _get_agent() -> Optional[OrchestratorAgent]:
    global _agent
    if _agent is not None:
        return _agent
    try:
        from src.main import rag_service

        _agent = OrchestratorAgent(rag_service=rag_service)
        return _agent
    except Exception as err:
        logger.error("CRITICAL: OrchestratorAgent failed to initialise: %s", err, exc_info=True)
        return None


@router.post("", response_model=ChatResponse)
async def process_stateless_chat(
    payload: ChatRequest, user: AuthenticatedUser = Depends(require_http_user)
):
    """Processes a single chat query with Firebase-authenticated user context."""
    agent = _get_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail=_AGENT_OFFLINE_MSG)

    try:
        answer = await agent.process_query(payload.query, user_id=user.uid)
        return ChatResponse(answer=answer)
    except RuntimeError as err:
        logger.error("Stateless chat failed: %s", err, exc_info=True)
        raise HTTPException(status_code=503, detail=str(err))
    except Exception:
        logger.error("Unexpected stateless chat error.", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal server error occurred.")


@router.get("/history/{session_id}")
async def get_session_history(
    session_id: str,
    limit: int = 200,
    user: AuthenticatedUser = Depends(require_http_user),
):
    """Return persisted session history for the authenticated user."""
    agent = _get_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail=_AGENT_OFFLINE_MSG)

    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500.")

    try:
        async with get_db_session() as db:
            await agent.history_repo.validate_session_owner(db, session_id, user.uid)
            rows = await agent.history_repo.get_history_records(db, user.uid, session_id, limit=limit)

        messages = [
            {
                "id": str(row.id),
                "session_id": row.session_id,
                "role": row.role,
                "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
        return {"session_id": session_id, "messages": messages}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden for this session.")
    except HTTPException:
        raise
    except Exception:
        logger.error("Failed to load session history.", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load session history.")


@router.websocket("/ws/{session_id}")
async def process_stateful_websocket_chat(websocket: WebSocket, session_id: str):
    """Delegates the full WebSocket lifecycle to a ChatSession with user isolation."""
    try:
        user = require_ws_user(websocket)
    except HTTPException as exc:
        await websocket.accept()
        await websocket.send_json({"type": "error", "detail": exc.detail})
        await websocket.close(code=1008, reason="Unauthorized")
        return

    agent = _get_agent()
    if agent is None:
        await websocket.accept()
        try:
            await websocket.send_json({"type": "error", "detail": _AGENT_OFFLINE_MSG})
            await websocket.close(code=1011, reason="Agent offline")
        except Exception:
            pass
        return

    session = ChatSession(websocket, session_id, user.uid, agent)
    await session.run()
