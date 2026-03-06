"""
Encapsulates WebSocket connection lifecycle and chat session state.

By isolating the transport layer (WebSocket send/receive/close) behind
a dedicated class, the router stays a thin dispatch layer and each
concern — idle timeout, message validation, stream relay, error
reporting — lives in a single, testable method.
"""

import asyncio
import logging
from typing import Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.core.database import get_db_session
from src.orchestration.agent import OrchestratorAgent

logger = logging.getLogger("milo-orchestrator.session")

_IDLE_TIMEOUT_SECONDS = 3600.0


class ChatSession:
    """
    State-encapsulated wrapper around a single WebSocket chat session.

    - Accept / close the underlying WebSocket with proper close codes.
    - Enforce an idle-timeout to reap zombie connections.
    - Validate inbound messages before handing them to the agent.
    - Relay the async LLM token stream back to the client.
    - Map every error domain to a single, consistent response shape.
    """

    def __init__(self, websocket: WebSocket, session_id: str, agent: OrchestratorAgent) -> None:
        self._ws = websocket
        self._session_id = session_id
        self._agent = agent


    async def run(self) -> None:
        """Single public entry point: accept → process turns → close."""
        await self._ws.accept()
        logger.info("Session '%s': WebSocket connected.", self._session_id)

        try:
            await self._conversation_loop()
        except WebSocketDisconnect as exc:
            logger.info("Session '%s': client disconnected (code=%s).", self._session_id, exc.code)
        except asyncio.CancelledError:
            logger.info("Session '%s': handler cancelled.", self._session_id)
        except Exception as exc:
            logger.error("Session '%s': unhandled error — %s", self._session_id, exc, exc_info=True)
            await self._close(detail="An internal server error occurred.")

    async def _conversation_loop(self) -> None:
        while True:
            user_text = await self._receive_message()
            if user_text is None:
                break  # idle-timeout → connection already closed

            # TODO improve the empty-message handling process
            if not user_text.strip():
                await self._send_error("Cannot process an empty message.")
                continue

            logger.info("Session '%s': received message: %s", self._session_id, user_text)
            await self._process_turn(user_text)

    async def _receive_message(self) -> Optional[str]:
        """Wait for the next client message, enforce the idle timeout."""
        try:
            return await asyncio.wait_for(self._ws.receive_text(), timeout=_IDLE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("Session '%s': idle timeout exceeded — closing.", self._session_id)
            await self._ws.close(code=1008, reason="Idle timeout exceeded")
            return None

    async def _send_json(self, payload: dict) -> bool:
        """Send a JSON frame if the socket is still open."""
        if self._ws.client_state != WebSocketState.CONNECTED:
            logger.debug("Session '%s': suppressing send on closed socket.", self._session_id)
            return False
        try:
            await self._ws.send_json(payload)
            return True
        except Exception as exc:
            logger.warning("Session '%s': send failed — %s", self._session_id, exc)
            return False

    async def _send_error(self, detail: str) -> bool:
        return await self._send_json({"type": "error", "detail": detail})

    async def _close(self, *, detail: str, code: int = 1011) -> None:
        """Send a final error frame and close the socket."""
        try:
            await self._send_error(detail)
            await self._ws.close(code=code, reason=detail[:123])
        except Exception:
            pass

    async def _process_turn(self, user_text: str) -> None:
        """Stream one agent response back to the client."""
        try:
            async with get_db_session() as db:
                stream = self._agent.process_session_stream(db, self._session_id, user_text)
                async for chunk in stream:
                    if not await self._send_json({"type": "chunk", "text": chunk}):
                        logger.info("Session '%s': client dropped mid-stream — halting.", self._session_id)
                        return

            await self._send_json({"type": "done"})
        except RuntimeError as exc:
            logger.error("Session '%s': agent error — %s", self._session_id, exc)
            await self._send_error(str(exc))
        except asyncio.CancelledError:
            logger.info("Session '%s': turn cancelled mid-flight.", self._session_id)
            raise
