import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, BackgroundTasks
from sqlalchemy import text
from src.core.database import get_db_session

from src.api.session import ChatSession
from src.core.auth import AuthenticatedUser, require_http_user, require_ws_user
from src.orchestration.agent import OrchestratorAgent, get_agent
from src.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger("milo-orchestrator.api")
router = APIRouter(prefix="/chat", tags=["Chat"])

_agent: Optional[OrchestratorAgent] = None
_AGENT_OFFLINE_MSG = "The chat orchestration service is currently unavailable."


@router.post("", response_model=ChatResponse)
async def process_stateless_chat(
    payload: ChatRequest,
    user: AuthenticatedUser = Depends(require_http_user),
    agent: OrchestratorAgent = Depends(get_agent),
):
    """Processes a single chat query with Firebase-authenticated user context."""
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


@router.post("/bootstrap-user")
async def bootstrap_authenticated_user(
    user: AuthenticatedUser = Depends(require_http_user),
    role: Optional[str] = None,
):
    """
    Ensure the authenticated Firebase user exists in relational users table.
    Safe to call repeatedly (idempotent upsert).
    Accepts an optional `role` query param ("teacher" or "student", default "student").
    """
    display_name = str(
        user.claims.get("name")
        or user.claims.get("displayName")
        or (user.email.split("@")[0] if user.email else "")
    ).strip()
    email = str(user.email or "").strip() or f"{user.uid}@milo.local"
    initial_role = role if role in ("teacher", "student") else "student"

    try:
        async with get_db_session() as db:
            from src.core.models import User
            result = await db.execute(
                text(
                    """
                    INSERT INTO users (id, email, display_name, role)
                    VALUES (:id, :email, :display_name, :role)
                    ON CONFLICT (id) DO UPDATE
                    SET email = EXCLUDED.email,
                        display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''), users.display_name)
                    RETURNING role, photo_data_url
                    """
                ),
                {"id": user.uid, "email": email, "display_name": display_name, "role": initial_role},
            )
            row = result.one()
            actual_role = row.role
            photo_data_url = row.photo_data_url
        return {
            "ok": True,
            "user_id": user.uid,
            "email": email,
            "display_name": display_name,
            "role": actual_role,
            "photo_data_url": photo_data_url,
        }
    except Exception:
        logger.error("Failed to bootstrap authenticated user into users table.", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to bootstrap user.")


@router.get("/history/{session_id}")
async def get_session_history(
    session_id: str,
    limit: int = 200,
    user: AuthenticatedUser = Depends(require_http_user),
    agent: OrchestratorAgent = Depends(get_agent),
):
    """Return persisted session history for the authenticated user."""
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
                "session_id": str(row.session_id) if row.session_id is not None else None,
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


@router.websocket("/activities/{activity_id}")
async def process_stateful_websocket_chat(
        websocket: WebSocket,
        activity_id: str,
        background_tasks: BackgroundTasks,
        agent: OrchestratorAgent = Depends(get_agent),
):
    """Delegates the full WebSocket lifecycle to a ChatSession with user isolation."""
    try:
        user = require_ws_user(websocket)
    except HTTPException as exc:
        await websocket.accept()
        await websocket.send_json({"type": "error", "detail": exc.detail})
        await websocket.close(code=1008, reason="Unauthorized")
        return

    if agent is None:
        await websocket.accept()
        try:
            await websocket.send_json({"type": "error", "detail": _AGENT_OFFLINE_MSG})
            await websocket.close(code=1011, reason="Agent offline")
        except Exception:
            pass
        return

    session = ChatSession(websocket, None, user.uid, agent, activity_id=activity_id, background_tasks=background_tasks)
    await session.run()
