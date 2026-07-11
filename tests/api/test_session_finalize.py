"""Closure-sentinel finalization path.

Covers the frame contract when the LLM ends a session: deterministic farewell
chunk + TTS audio, `done`, `session_complete`, then an active server-side
WebSocket close — plus the loop guards that keep a finalized session from
processing further frames.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocketDisconnect, WebSocketState


def _make_ws():
    ws = MagicMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.application_state = WebSocketState.CONNECTED
    ws.send_json = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)
    return ws


def _make_session(ws, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    from src.api.session import ChatSession

    agent = MagicMock()
    session = ChatSession(
        websocket=ws,
        session_id="test-session",
        user_id="dev-user",
        agent=agent,
        activity_id="test-activity",
    )
    session._session_id = "test-session"
    session._session_id_uuid = None  # stateless mode: skip Step 7 DB writes
    session._context_description = ""
    session._metrics = MagicMock()
    return session


def _make_decision(*, closure_eligible: bool):
    from src.policy.types import FSMState, HintLadderState, RecoveryState

    decision = MagicMock()
    decision.closure_eligible = closure_eligible
    decision.plan.prompt_directives = []
    decision.plan.question_id = "q1"
    decision.plan.question_text = "¿Y ahora?"
    decision.scores = None
    decision.debug_trace = None
    decision.next_state = FSMState.MONITORING
    decision.applied_rules = []
    decision.next_hint_state = HintLadderState.PROCESS_FEEDBACK
    decision.next_recovery_state = RecoveryState.NORMAL
    decision.next_turns_in_hint_state = 0
    decision.next_consecutive_low_struggle_turns = 0
    decision.next_turns_in_recovery = 0
    decision.next_turns_since_meta_feedback = 99
    decision.next_turns_since_procedural_unblock = 99
    return decision


def _wire_process_turn(session, monkeypatch, *, decision, stream_chunks, history_len):
    """Monkeypatch the collaborators _process_turn touches so it can run
    against a fake agent/DB/policy engine."""
    import src.api.session as sess_mod

    @asynccontextmanager
    async def fake_db_session():
        yield MagicMock()

    monkeypatch.setattr(sess_mod, "get_db_session", fake_db_session)
    monkeypatch.setattr(
        sess_mod._policy_engine, "evaluate",
        lambda ctx, collect_trace=False: decision,
    )
    session._agent.history_repo.get_history = AsyncMock(
        return_value=[{}] * history_len
    )

    async def fake_stream():
        for chunk in stream_chunks:
            yield chunk

    session._agent.process_session_stream = MagicMock(
        side_effect=lambda *a, **k: fake_stream()
    )


def _sent_types(ws):
    return [call.args[0].get("type") for call in ws.send_json.await_args_list]


@pytest.mark.asyncio
async def test_sentinel_sends_farewell_then_done_then_complete_then_close(monkeypatch):
    import src.api.session as sess_mod
    from src.api.session import _CLOSURE_FAREWELL
    from src.policy.engine import CLOSURE_SENTINEL

    ws = _make_ws()
    session = _make_session(ws, monkeypatch)
    _wire_process_turn(
        session, monkeypatch,
        decision=_make_decision(closure_eligible=True),
        stream_chunks=["Gracias por reflexionar hoy. ", CLOSURE_SENTINEL],
        history_len=10,  # turn_count = 5 >= CLOSURE_MIN_TURNS
    )
    monkeypatch.setattr(sess_mod.tts, "synthesize", AsyncMock(return_value=b"mp3"))

    await session._process_turn("creo que ya entendí todo", mode="voice")

    assert _sent_types(ws) == ["chunk", "audio_sentence", "done", "session_complete"]
    chunk_frame = ws.send_json.await_args_list[0].args[0]
    assert chunk_frame["text"] == _CLOSURE_FAREWELL
    # The sentinel must never reach the client in any frame.
    for call in ws.send_json.await_args_list:
        assert CLOSURE_SENTINEL not in json.dumps(call.args[0])
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs.get("code") == 1000
    assert session._should_close is True


@pytest.mark.asyncio
async def test_farewell_tts_failure_degrades_silently(monkeypatch):
    import src.api.session as sess_mod
    from src.api.session import _CLOSURE_FAREWELL
    from src.policy.engine import CLOSURE_SENTINEL
    from src.services import tts

    ws = _make_ws()
    session = _make_session(ws, monkeypatch)
    _wire_process_turn(
        session, monkeypatch,
        decision=_make_decision(closure_eligible=True),
        stream_chunks=["Listo. ", CLOSURE_SENTINEL],
        history_len=10,
    )
    monkeypatch.setattr(
        sess_mod.tts, "synthesize",
        AsyncMock(side_effect=tts.TTSError("edge-tts down")),
    )

    await session._process_turn("ya está", mode="voice")

    # Text farewell still goes out; audio silently skipped; close still happens.
    assert _sent_types(ws) == ["chunk", "done", "session_complete"]
    assert ws.send_json.await_args_list[0].args[0]["text"] == _CLOSURE_FAREWELL
    ws.close.assert_awaited_once()
    assert session._should_close is True


@pytest.mark.asyncio
async def test_stray_sentinel_below_min_turns_no_finalize(monkeypatch):
    import src.api.session as sess_mod
    from src.api.session import _CLOSURE_FAREWELL
    from src.policy.engine import CLOSURE_SENTINEL

    ws = _make_ws()
    session = _make_session(ws, monkeypatch)
    _wire_process_turn(
        session, monkeypatch,
        decision=_make_decision(closure_eligible=False),
        # Non-eligible streaming path: sentinel stripped by _strip_sentinel_tail.
        stream_chunks=["Hola, sigamos pensando juntos. ", CLOSURE_SENTINEL],
        history_len=2,  # turn_count = 1 < CLOSURE_MIN_TURNS
    )
    monkeypatch.setattr(sess_mod.tts, "synthesize", AsyncMock(return_value=b"mp3"))

    await session._process_turn("hola", mode="voice")

    types = _sent_types(ws)
    assert "session_complete" not in types
    assert "chunk" in types  # normal content streamed to the client
    for call in ws.send_json.await_args_list:
        frame = call.args[0]
        assert CLOSURE_SENTINEL not in json.dumps(frame)
        assert frame.get("text") != _CLOSURE_FAREWELL
    ws.close.assert_not_awaited()
    assert session._should_close is False


@pytest.mark.asyncio
async def test_loop_exits_promptly_after_finalize(monkeypatch):
    """The closing turn (a background task) must unblock the receive loop by
    closing the socket — the loop may not sit in receive_text until the idle
    timeout."""
    from src.api.session import ChatSession

    ws = _make_ws()
    finalized = asyncio.Event()

    async def fake_process_turn(self, text, mode="text"):
        self._should_close = True
        finalized.set()  # stands in for ws.close() waking the receive

    monkeypatch.setattr(ChatSession, "_process_turn", fake_process_turn)

    incoming = [json.dumps({"type": "message", "text": "chau"})]

    async def fake_receive_text():
        if incoming:
            return incoming.pop(0)
        await finalized.wait()
        raise WebSocketDisconnect(code=1000)

    ws.receive_text = AsyncMock(side_effect=fake_receive_text)
    session = _make_session(ws, monkeypatch)

    loop_task = asyncio.create_task(session._conversation_loop())
    # Must finish well under the 3600s idle timeout.
    done, pending = await asyncio.wait({loop_task}, timeout=2.0)
    assert loop_task in done, "conversation loop did not exit after finalize"
    # Either a clean break (flag seen at loop top) or the disconnect raised by
    # the server-side close — both are prompt exits handled by run().
    exc = loop_task.exception()
    assert exc is None or isinstance(exc, WebSocketDisconnect)


@pytest.mark.asyncio
async def test_message_after_finalize_is_ignored(monkeypatch):
    """A message frame racing the finalization must not spawn a new turn."""
    from src.api.session import ChatSession

    ws = _make_ws()
    turn_calls: list[str] = []

    async def fake_process_turn(self, text, mode="text"):
        turn_calls.append(text)

    monkeypatch.setattr(ChatSession, "_process_turn", fake_process_turn)

    session = _make_session(ws, monkeypatch)

    async def fake_receive_text():
        # Finalization lands while the loop is parked in receive: the frame
        # is already in flight when _should_close flips.
        session._should_close = True
        return json.dumps({"type": "message", "text": "una cosa más"})

    ws.receive_text = AsyncMock(side_effect=fake_receive_text)

    await asyncio.wait_for(session._conversation_loop(), timeout=2.0)

    assert turn_calls == []
    assert session._active_turn_task is None
