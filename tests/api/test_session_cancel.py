import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_session(ws, monkeypatch):
    """Build a minimal ChatSession with a fake WebSocket and no real agent/DB."""
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    from src.api.session import ChatSession
    from unittest.mock import MagicMock

    agent = MagicMock()
    session = ChatSession(
        websocket=ws,
        session_id="test-session",
        user_id="dev-user",
        agent=agent,
        activity_id="test-activity",
    )
    # Pre-populate fields that _process_turn reads but we monkeypatch away anyway
    session._session_id = "test-session"
    session._context_description = ""
    return session


@pytest.mark.asyncio
async def test_session_cancel_turn_cancels_in_flight_process_turn(monkeypatch):
    """When cancel_turn arrives mid-turn, _process_turn is cancelled."""
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    from src.api.session import ChatSession
    from starlette.websockets import WebSocketState

    # Make _process_turn a slow coroutine we can interrupt.
    process_called = asyncio.Event()
    process_cancelled = asyncio.Event()

    async def slow_process_turn(self, text):
        process_called.set()
        try:
            await asyncio.sleep(10)  # would hang forever
        except asyncio.CancelledError:
            process_cancelled.set()
            raise

    monkeypatch.setattr(ChatSession, "_process_turn", slow_process_turn)

    # Fake WS that yields one message then one cancel_turn then a close.
    incoming = [
        json.dumps({"type": "message", "text": "hello"}),
        json.dumps({"type": "cancel_turn"}),
    ]
    closed = asyncio.Event()

    async def fake_receive_text():
        if not incoming:
            await closed.wait()
            raise RuntimeError("WS closed")
        return incoming.pop(0)

    ws = MagicMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.receive_text = AsyncMock(side_effect=fake_receive_text)
    ws.send_json = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)

    session = _make_session(ws, monkeypatch)

    # Run the receive loop briefly.
    async def driver():
        await process_called.wait()
        # cancel_turn is the next frame; give the loop a moment to process it
        await asyncio.sleep(0.05)
        closed.set()

    loop_task = asyncio.create_task(session._conversation_loop())
    drive_task = asyncio.create_task(driver())

    try:
        await asyncio.wait_for(process_cancelled.wait(), timeout=2.0)
    finally:
        loop_task.cancel()
        drive_task.cancel()
        await asyncio.gather(loop_task, drive_task, return_exceptions=True)

    assert process_cancelled.is_set()
    # The session should also have emitted a "cancelled" frame.
    sent_types = [call.args[0].get("type") for call in ws.send_json.await_args_list]
    assert "cancelled" in sent_types


@pytest.mark.asyncio
async def test_session_accepts_legacy_plain_text_as_message(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    from src.api.session import ChatSession
    from starlette.websockets import WebSocketState

    seen_text: list[str] = []

    async def capture_process_turn(self, text):
        seen_text.append(text)

    monkeypatch.setattr(ChatSession, "_process_turn", capture_process_turn)

    closed = asyncio.Event()
    incoming = ["hello world"]  # plain string, NOT JSON

    async def fake_receive_text():
        if not incoming:
            await closed.wait()
            raise RuntimeError("WS closed")
        return incoming.pop(0)

    ws = MagicMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.receive_text = AsyncMock(side_effect=fake_receive_text)
    ws.send_json = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)

    session = _make_session(ws, monkeypatch)

    loop_task = asyncio.create_task(session._conversation_loop())
    await asyncio.sleep(0.1)
    closed.set()
    loop_task.cancel()
    await asyncio.gather(loop_task, return_exceptions=True)

    assert seen_text == ["hello world"]


@pytest.mark.asyncio
async def test_session_cancel_turn_with_no_active_turn_is_noop(monkeypatch):
    """cancel_turn when nothing is in flight should not raise and should
    NOT emit a 'cancelled' frame (the receive loop logs and continues)."""
    from starlette.websockets import WebSocketState

    closed = asyncio.Event()
    incoming = [json.dumps({"type": "cancel_turn"})]

    async def fake_receive_text():
        if not incoming:
            await closed.wait()
            raise RuntimeError("WS closed")
        return incoming.pop(0)

    ws = MagicMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.receive_text = AsyncMock(side_effect=fake_receive_text)
    ws.send_json = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)

    session = _make_session(ws, monkeypatch)

    loop_task = asyncio.create_task(session._conversation_loop())
    await asyncio.sleep(0.1)
    closed.set()
    loop_task.cancel()
    await asyncio.gather(loop_task, return_exceptions=True)

    # No "cancelled" frame should have been sent (nothing was cancelled).
    sent_types = [call.args[0].get("type") for call in ws.send_json.await_args_list]
    assert "cancelled" not in sent_types
