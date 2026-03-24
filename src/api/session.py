"""
Encapsulates WebSocket connection lifecycle and chat session state.
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
    State wrapper around one WebSocket chat session.
    """

    def __init__(
        self, websocket: WebSocket, session_id: str, user_id: str, agent: OrchestratorAgent
    ) -> None:
        self._ws = websocket
        self._session_id = session_id
        self._user_id = user_id
        self._agent = agent

    async def run(self) -> None:
        await self._ws.accept()
        logger.info(
            "Session '%s': WebSocket connected for user=%s.", self._session_id, self._user_id
        )

        try:
            await self._conversation_loop()
        except WebSocketDisconnect as exc:
            logger.info("Session '%s': client disconnected (code=%s).", self._session_id, exc.code)
        except asyncio.CancelledError:
            logger.info("Session '%s': handler cancelled.", self._session_id)
        except Exception as exc:
            logger.error("Session '%s': unhandled error - %s", self._session_id, exc, exc_info=True)
            await self._close(detail="An internal server error occurred.")

    async def _conversation_loop(self) -> None:
        while True:
            user_text = await self._receive_message()
            if user_text is None:
                break

            if not user_text.strip():
                await self._send_error("Cannot process an empty message.")
                continue

            logger.info("Session '%s': received message.", self._session_id)
            await self._process_turn(user_text)

    async def _receive_message(self) -> Optional[str]:
        try:
            return await asyncio.wait_for(self._ws.receive_text(), timeout=_IDLE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("Session '%s': idle timeout exceeded - closing.", self._session_id)
            await self._ws.close(code=1008, reason="Idle timeout exceeded")
            return None

    async def _send_json(self, payload: dict) -> bool:
        if self._ws.client_state != WebSocketState.CONNECTED:
            logger.debug("Session '%s': suppressing send on closed socket.", self._session_id)
            return False
        try:
            await self._ws.send_json(payload)
            return True
        except Exception as exc:
            logger.warning("Session '%s': send failed - %s", self._session_id, exc)
            return False

    async def _send_error(self, detail: str) -> bool:
        return await self._send_json({"type": "error", "detail": detail})

    async def _close(self, *, detail: str, code: int = 1011) -> None:
        try:
            await self._send_error(detail)
            await self._ws.close(code=code, reason=detail[:123])
        except Exception:
            pass

    async def _process_turn(self, user_text: str) -> None:
        try:
            async with get_db_session() as db:
                stream = self._agent.process_session_stream(
                    db, self._user_id, self._session_id, user_text
                )
                async for chunk in stream:
                    if not await self._send_json({"type": "chunk", "text": chunk}):
                        logger.info(
                            "Session '%s': client dropped mid-stream - halting.", self._session_id
                        )
                        return

            await self._send_json({"type": "done"})
        except PermissionError:
            await self._send_error("No tenes permiso para acceder a esta conversacion.")
        except RuntimeError as exc:
            logger.error("Session '%s': agent error - %s", self._session_id, exc)
            await self._send_error(str(exc))
        except asyncio.CancelledError:
            logger.info("Session '%s': turn cancelled mid-flight.", self._session_id)
            raise
