# Voice I/O — PR 3 (barge-in + voice session opt-in) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a student interrupt Milo mid-response by clicking the mic again (cancels the in-flight LLM/audio stream, flushes the audio queue, starts recording), and gate audio playback behind an explicit "Start voice session" button so browser autoplay restrictions can't silently swallow the first audio of a fresh session.

**Architecture:** Backend receives a new `{type: "cancel_turn"}` WebSocket frame (existing protocol switches from plaintext to typed-JSON, with backward-compat for raw strings → treated as `{type:"message",text:"..."}`). The session's `_process_turn` runs as a backgrounded `asyncio.Task` so the receive loop is free to dispatch cancellation. On cancel, the task is `task.cancel()`'d; `audio_stream.wrap` and `_process_turn` propagate `CancelledError` cleanly, emit a `{type:"cancelled"}` frame, save whatever response was accumulated, and return. On the frontend, a new `<StartVoiceSessionButton>` toggles a `voiceSessionStarted` state in `App.js` — the `AudioQueue` only enqueues audio when that state is true (so audio for the greeting / mid-stream doesn't try to autoplay before the user has clicked anything). `<MicButton>` calls a new `onBeforeRecord` prop (provided by `App.js`) that sends `cancel_turn` and flushes the audio queue before recording begins.

**Tech Stack:**
- Backend: FastAPI, Python `asyncio` (CancelScope-style cancellation via `Task.cancel()`), pytest, pytest-asyncio.
- Frontend: React 19, existing WS client.
- Spec: [docs/specs/2026-05-17-voice-io-design.md](../specs/2026-05-17-voice-io-design.md) — decisions Q10 (barge-in: cancel + interrupt) and Q12 (opt-in voice session button gating).
- Prior PRs: PR 1 STT (branch `feat/voice-io-pr1-stt`, merged into `feat/voice-io-pr2-tts`), PR 2 TTS sentence-streaming (`feat/voice-io-pr2-tts`).

---

## File Structure

**Backend — create:**
- `tests/api/test_session_cancel.py` — integration tests for cancel-turn dispatch and partial-response saving.

**Backend — modify:**
- `src/api/session.py` — receive loop now parses JSON frames, dispatches `cancel_turn`; `_process_turn` becomes cancellation-aware (catches `CancelledError`, persists partial response, emits `cancelled` frame). Extract a `_audio_frame(seq, mp3_bytes, voice)` helper (PR 2 follow-up cleanup — the frame dict was duplicated in two places).
- `src/api/audio_stream.py` — no logic change, but a small docstring comment confirming that `CancelledError` from `tts.synthesize` propagates out of `wrap()` cleanly (it already does — async generators propagate cancellations from the consumer).

**Frontend — create:**
- `milo-front/src/components/StartVoiceSessionButton.js` — single-purpose button that toggles `voiceSessionStarted` and pre-warms an empty `Audio()` `.play()` call to register the user gesture for browser autoplay.
- `milo-front/src/components/__tests__/StartVoiceSessionButton.test.js`

**Frontend — modify:**
- `milo-front/src/api/sessionSocket.js` — add `sendCancelTurn()` method that sends `{"type": "cancel_turn"}`; switch `sendMessage` to send `{"type": "message", "text": "..."}` (backend will accept both shapes during the transition).
- `milo-front/src/api/apiClient.js` — expose `cancelInflightTurn()` that forwards to the active SessionSocket.
- `milo-front/src/App.js` — add `voiceSessionStarted` state, mount the button, gate `audioQueueRef.current.enqueue()` so it only fires when the state is true, expose `onBeforeRecord` (which calls `apiClient.cancelInflightTurn()` + `audioQueueRef.current.flush()`) to ChatComposer / MicButton.
- `milo-front/src/components/ChatComposer.js` — pass `onBeforeRecord` through to `MicButton`.
- `milo-front/src/components/MicButton.js` — call `onBeforeRecord` (if provided) at the start of the start-recording branch.

**PR 3 deliberate simplifications:**
- No barge-in DURING recording (mid-record clicks just stop the recording). Only the start-recording moment triggers the cancel.
- No interceptor on cancelled turns — the policy interceptor only fires when the LLM stream completes normally.
- `StartVoiceSessionButton` is a simple toggle; we don't track which gesture unlocked autoplay across reloads (state is per-component-mount).
- The protocol migration to JSON frames keeps a plain-text fallback for one PR. If a clean cutover is preferred, drop the fallback in PR 4.

---

## Task 1: Backend — typed JSON frames + cancel_turn dispatch (TDD)

**Files:**
- Create: `tests/api/test_session_cancel.py`
- Modify: `src/api/session.py` (around lines 240-269 — the receive loop and `_receive_message` helper)

The current loop blocks on `await self._process_turn(user_text)`, so cancel can never be received mid-turn. We'll:
1. Parse each incoming WS frame as JSON; backward-compat for plain strings (treat as `message`).
2. Dispatch `message` → spawn `_process_turn` as a backgrounded task.
3. Dispatch `cancel_turn` → call `task.cancel()` on the active task.
4. The receive loop stops awaiting individual turns and instead loops on `receive`, letting cancel arrive mid-turn.

### Step 1: Write a failing test for "cancel_turn cancels the in-flight task"

Create `tests/api/test_session_cancel.py`:

```python
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_session_cancel_turn_cancels_in_flight_process_turn(monkeypatch):
    """When cancel_turn arrives mid-turn, _process_turn is cancelled."""
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    from src.api.session import Session

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

    monkeypatch.setattr(Session, "_process_turn", slow_process_turn)

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
    ws.client_state = 1  # WebSocketState.CONNECTED
    ws.receive_text = AsyncMock(side_effect=fake_receive_text)
    ws.send_json = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)

    session = Session(
        ws=ws,
        user_id="dev-user",
        activity_id="test-activity",
        session_id="test-session",
        context_description="",
    )

    # Run the receive loop briefly.
    async def driver():
        await process_called.wait()
        # cancel_turn is the next frame
        await asyncio.sleep(0.05)  # let cancel propagate
        closed.set()

    loop_task = asyncio.create_task(session._ws_loop())
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
```

Note: this test exercises the receive loop directly. If `Session`'s constructor or `_ws_loop` are named differently in your file, adapt the imports — but the spec of behavior is what matters.

### Step 2: Run — expect FAIL

```bash
cd /Users/saints/Desktop/ITBA/PF/milo-back-agent-orchestrator
.venv/bin/python3.11 -m pytest tests/api/test_session_cancel.py::test_session_cancel_turn_cancels_in_flight_process_turn -v
```

Expected: FAIL — current loop blocks on `await self._process_turn` so cancel_turn is never read.

### Step 3: Refactor `_receive_message` + receive loop in `session.py`

Open `src/api/session.py`. Find the receive loop (around lines 240-269). It currently looks roughly like:

```python
async def _ws_loop(self):
    # ... initial handshake / greeting ...
    while True:
        user_text = await self._receive_message()
        if user_text is None:
            return
        if not user_text.strip():
            await self._send_error("Cannot process an empty message.")
            continue
        logger.info("Session '%s': received message.", self._session_id)
        await self._process_turn(user_text)

async def _receive_message(self) -> Optional[str]:
    try:
        return await asyncio.wait_for(self._ws.receive_text(), timeout=_IDLE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        ...
```

Replace with:

```python
async def _ws_loop(self):
    # ... initial handshake / greeting (unchanged) ...
    while True:
        frame = await self._receive_frame()
        if frame is None:
            # Idle timeout / closed
            if self._active_turn_task and not self._active_turn_task.done():
                self._active_turn_task.cancel()
                await asyncio.gather(self._active_turn_task, return_exceptions=True)
            return

        ftype = frame.get("type")

        if ftype == "cancel_turn":
            if self._active_turn_task and not self._active_turn_task.done():
                logger.info("Session '%s': cancel_turn received — cancelling in-flight turn.", self._session_id)
                self._active_turn_task.cancel()
            else:
                logger.info("Session '%s': cancel_turn received — no active turn to cancel.", self._session_id)
            continue

        if ftype == "message":
            user_text = (frame.get("text") or "").strip()
            if not user_text:
                await self._send_error("Cannot process an empty message.")
                continue

            # If a previous turn is still running, cancel it before starting a new one.
            # (Simpler than queuing and matches the "barge-in" intent.)
            if self._active_turn_task and not self._active_turn_task.done():
                self._active_turn_task.cancel()
                await asyncio.gather(self._active_turn_task, return_exceptions=True)

            logger.info("Session '%s': received message.", self._session_id)
            self._active_turn_task = asyncio.create_task(self._process_turn(user_text))
            # Don't await — let the receive loop continue so cancel_turn can arrive.
            continue

        await self._send_error(f"Unknown frame type: {ftype!r}")


async def _receive_frame(self) -> Optional[dict]:
    """Receive one WS frame as JSON. Plain-text frames (legacy clients) are
    treated as {"type": "message", "text": <the text>} for backward compatibility.
    """
    try:
        raw = await asyncio.wait_for(self._ws.receive_text(), timeout=_IDLE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.info("Session '%s': idle timeout exceeded - closing.", self._session_id)
        await self._ws.close(code=1008, reason="Idle timeout exceeded")
        return None

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "type" in parsed:
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    # Fallback: legacy plain-text message.
    return {"type": "message", "text": raw}
```

Add `import json` at the top of `session.py` if not already present.

Add a new instance attribute initialization (find the `__init__` or the field-defaults section near the top of `Session`):

```python
    self._active_turn_task: Optional[asyncio.Task] = None
```

Keep the old `_receive_message` method as a thin wrapper that calls `_receive_frame` and returns the text (or delete it entirely — there should be no callers left).

### Step 4: Make `_process_turn` cancellation-aware

Inside `_process_turn` (around line 292+), wrap the body in a try/except for `CancelledError`:

```python
async def _process_turn(self, user_text: str) -> None:
    user_msg_ts = time.time()
    observed_latency: Optional[float] = (
        max(0.0, user_msg_ts - self._last_milo_response_ts)
        if self._last_milo_response_ts is not None
        else None
    )

    accumulated: List[str] = []  # promoted out of the inner try so the except can persist it

    try:
        # ... existing body up to and including the stream loop ...
        # (keep the original code; just keep `accumulated` in scope for the except)
        ...

    except asyncio.CancelledError:
        logger.info(
            "Session '%s': turn cancelled after %d accumulated chars.",
            self._session_id, sum(len(c) for c in accumulated),
        )
        # Persist whatever the agent produced so the student's screen and DB stay consistent.
        if accumulated:
            try:
                async with get_db_session() as db:
                    await self._agent.save_partial_response(db, self._session_id, "".join(accumulated))
            except Exception as exc:
                logger.warning("Session '%s': partial save failed: %s", self._session_id, exc)

        await self._send_json({"type": "cancelled"})
        raise  # propagate so the awaiting code in _ws_loop knows
```

If the agent doesn't have a `save_partial_response` method, add one in `src/orchestration/agent.py`:

```python
async def save_partial_response(self, db, session_id: str, content: str) -> None:
    """Persist a partial agent response (cancelled mid-stream). Same shape as
    the normal save path; marker is implicit via length / cancelled frame.
    """
    if not content.strip():
        return
    # Use the same model save helper as the normal path. If unsure, inline
    # the SQL used in process_session_stream's normal completion code path.
    await self._save_message(db, session_id, role="assistant", content=content)
```

If `_save_message` doesn't exist on `OrchestratorAgent`, look at how `process_session_stream` writes the completed assistant response to the DB on success (you'll find a `Message` insert near the end). Wrap that into a helper or just inline-call the same SQLAlchemy `db.add(Message(...))` / `db.commit()` shape from the cancel path.

### Step 5: Run — expect PASS

```bash
.venv/bin/python3.11 -m pytest tests/api/test_session_cancel.py::test_session_cancel_turn_cancels_in_flight_process_turn -v
```

Expected: PASS.

### Step 6: Add a failing test for "plain text is still accepted as a message"

Append to `tests/api/test_session_cancel.py`:

```python
@pytest.mark.asyncio
async def test_session_accepts_legacy_plain_text_as_message(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    from src.api.session import Session

    seen_text: list[str] = []

    async def capture_process_turn(self, text):
        seen_text.append(text)

    monkeypatch.setattr(Session, "_process_turn", capture_process_turn)

    closed = asyncio.Event()
    incoming = ["hello world"]  # plain string, NOT JSON

    async def fake_receive_text():
        if not incoming:
            await closed.wait()
            raise RuntimeError("WS closed")
        return incoming.pop(0)

    ws = MagicMock()
    ws.client_state = 1
    ws.receive_text = AsyncMock(side_effect=fake_receive_text)
    ws.send_json = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)

    session = Session(
        ws=ws,
        user_id="dev-user",
        activity_id="test-activity",
        session_id="test-session",
        context_description="",
    )

    loop_task = asyncio.create_task(session._ws_loop())
    await asyncio.sleep(0.1)
    closed.set()
    loop_task.cancel()
    await asyncio.gather(loop_task, return_exceptions=True)

    assert seen_text == ["hello world"]
```

### Step 7: Run — expect PASS

```bash
.venv/bin/python3.11 -m pytest tests/api/test_session_cancel.py::test_session_accepts_legacy_plain_text_as_message -v
```

Expected: PASS (the fallback in `_receive_frame` handles this).

### Step 8: Run full backend suite — confirm no regressions

```bash
.venv/bin/python3.11 -m pytest -v 2>&1 | tail -5
```

Expected: all passing, including PR 2's 291+8 tests.

### Step 9: Commit

```bash
git add tests/api/test_session_cancel.py src/api/session.py src/orchestration/agent.py
git commit -m "[Feat] Add cancel_turn WS frame handler with task-based cancellation"
```

(Adjust `git add` if `agent.py` wasn't touched.)

---

## Task 2: Backend — extract `_audio_frame` helper (PR 2 follow-up cleanup)

**Files:**
- Modify: `src/api/session.py`

PR 2 left a duplicated dict literal for the `audio_sentence` frame in two places (the stream loop and the interceptor block). The PR 3 cancel work will touch this code path again; extracting the helper now keeps the diff for Task 1 cleaner.

### Step 1: Find the two literals

In `src/api/session.py`, find the two places that build:

```python
frame = {
    "type": "audio_sentence",
    "seq": event.seq,
    "mime": "audio/mp3",
    "voice": event.voice,
    "data": base64.b64encode(event.mp3_bytes).decode("ascii"),
}
```

and

```python
await self._send_json({
    "type": "audio_sentence",
    "seq": last_audio_seq + 1,
    "mime": "audio/mp3",
    "voice": voice_for_correction,
    "data": base64.b64encode(correction_mp3).decode("ascii"),
})
```

### Step 2: Add a module-level helper near the top of the file

After the imports, before the `Session` class:

```python
def _audio_frame(seq: int, mp3_bytes: bytes, voice: str) -> dict:
    """Wire format for an audio_sentence WS frame. Single source of truth so
    cancel/normal/interceptor paths all match.
    """
    return {
        "type": "audio_sentence",
        "seq": seq,
        "mime": "audio/mp3",
        "voice": voice,
        "data": base64.b64encode(mp3_bytes).decode("ascii"),
    }
```

### Step 3: Replace both call sites

In the stream loop, replace the `frame = {...}` and the `if not await self._send_json(frame):` lines with:

```python
                        if not await self._send_json(_audio_frame(event.seq, event.mp3_bytes, event.voice)):
                            logger.info(
                                "Session '%s': client dropped during audio - halting.", self._session_id
                            )
                            return
```

In the interceptor block, replace the inline dict with:

```python
                        await self._send_json(_audio_frame(last_audio_seq + 1, correction_mp3, voice_for_correction))
```

### Step 4: Run the suite — expect no regressions

```bash
.venv/bin/python3.11 -m pytest -v 2>&1 | tail -3
```

Expected: same pass count as before Task 2 (since this is a pure refactor).

### Step 5: Commit

```bash
git add src/api/session.py
git commit -m "[Refactor] Extract _audio_frame helper to dedupe audio_sentence builders"
```

---

## Task 3: Frontend — `sessionSocket.sendCancelTurn()` + JSON message wire (TDD)

**Files:**
- Modify: `milo-front/src/api/sessionSocket.js`

The current client sends `socket.send(text || '')` — raw string. We'll switch to JSON for messages and add a separate cancel method.

### Step 1: Find the existing `sendMessage` method

In `src/api/sessionSocket.js` around line 197:

```javascript
  sendMessage(text, onToken) {
    if (!this.isOpen) {
      return Promise.reject(new Error('WebSocket is not open. Cannot send message.'));
    }

    if (this._pendingSend) {
      return Promise.reject(
        new Error('A message is already being processed. Please wait for the current response.')
      );
    }

    return new Promise((resolve, reject) => {
      this._pendingSend = { resolve, reject, onToken, fullText: '' };
      this._socket.send(text || '');
    });
  }
```

### Step 2: Replace the send line to use JSON

Change:

```javascript
      this._socket.send(text || '');
```

to:

```javascript
      this._socket.send(JSON.stringify({ type: 'message', text: text || '' }));
```

### Step 3: Add `sendCancelTurn` method right below `sendMessage`

```javascript
  /**
   * Cancel the in-flight turn (if any). The server is permissive — sending
   * cancel_turn when no turn is active is a no-op. Does NOT reject the pending
   * sendMessage promise; the server will emit a `cancelled` frame which the
   * receive handler treats as a terminal frame.
   */
  sendCancelTurn() {
    if (!this.isOpen) return;
    this._socket.send(JSON.stringify({ type: 'cancel_turn' }));
  }
```

### Step 4: Handle the new `cancelled` frame in the receive switch

In the `_handleFrame` switch (around line 257 where it dispatches `chunk` / `done` / `error`), inside the `_pendingSend` block, add a case for `cancelled`:

```javascript
        if (frame.type === 'cancelled') {
          const fullText = this._pendingSend.fullText;
          this._pendingSend = null;
          resolve(fullText);  // resolve with partial; UI shows whatever was streamed
          return;
        }
```

Place it adjacent to the `if (frame.type === 'done')` branch.

### Step 5: Run all frontend tests — no regressions

From `milo-front/`:

```bash
CI=true npm test -- --watchAll=false
```

Expected: all 22 tests still pass (no test of `sendCancelTurn` exists yet — covered manually in smoke).

### Step 6: Commit

```bash
git add src/api/sessionSocket.js
git commit -m "[Feat] Send sendMessage as JSON and add sendCancelTurn for barge-in"
```

---

## Task 4: Frontend — `apiClient.cancelInflightTurn()`

**Files:**
- Modify: `milo-front/src/api/apiClient.js`

### Step 1: Find the existing `openSession` / `closeSession` block

Around line 406-440 in `apiClient.js`. There's an `openSession`, `closeSession`, and an `_activeSession` field on the apiClient.

### Step 2: Add a new method below `closeSession`

```javascript
  /**
   * Cancel the in-flight turn on the active session, if any. Safe no-op when
   * no session is active.
   */
  cancelInflightTurn() {
    if (this._activeSession && typeof this._activeSession.sendCancelTurn === 'function') {
      this._activeSession.sendCancelTurn();
    }
  },
```

### Step 3: Run frontend tests — no regressions

```bash
CI=true npm test -- --watchAll=false
```

### Step 4: Commit

```bash
git add src/api/apiClient.js
git commit -m "[Feat] Expose apiClient.cancelInflightTurn for barge-in callers"
```

---

## Task 5: Frontend — `<StartVoiceSessionButton>` component (TDD)

**Files:**
- Create: `milo-front/src/components/StartVoiceSessionButton.js`
- Create: `milo-front/src/components/__tests__/StartVoiceSessionButton.test.js`

A single-purpose button. When clicked, calls `onStart()` AND attempts to play a one-frame silent `Audio()` to register the user gesture for browser autoplay.

### Step 1: Write the failing test

Create `src/components/__tests__/StartVoiceSessionButton.test.js`:

```javascript
import { render, screen, fireEvent } from '@testing-library/react';
import StartVoiceSessionButton from '../StartVoiceSessionButton';

beforeEach(() => {
  global.Audio = class FakeAudio {
    constructor() {
      this.onended = null;
    }
    play() {
      return Promise.resolve();
    }
  };
});

test('renders a button labelled "Start voice session"', () => {
  render(<StartVoiceSessionButton onStart={() => {}} />);
  expect(screen.getByRole('button', { name: /start voice session/i })).toBeInTheDocument();
});

test('calls onStart when clicked', () => {
  const onStart = jest.fn();
  render(<StartVoiceSessionButton onStart={onStart} />);
  fireEvent.click(screen.getByRole('button'));
  expect(onStart).toHaveBeenCalledTimes(1);
});

test('does not render when active is true', () => {
  const { container } = render(<StartVoiceSessionButton active onStart={() => {}} />);
  expect(container).toBeEmptyDOMElement();
});
```

### Step 2: Run — expect FAIL (module not found)

```bash
CI=true npm test -- --watchAll=false StartVoiceSessionButton
```

### Step 3: Create the component

Create `src/components/StartVoiceSessionButton.js`:

```javascript
// 1×1 silent MP3 (base64) — used to pre-warm browser autoplay on user gesture.
// Source: standard "tiny silent mp3" used by JS audio libraries.
const SILENT_MP3 =
  'data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4LjI5LjEwMAAAAAAAAAAAAAAA//tQwAAAAAAAAAAAAAAAAAAAAAAASW5mbwAAAA8AAAACAAAAaAAA';

export default function StartVoiceSessionButton({ active = false, onStart }) {
  if (active) return null;

  function handleClick() {
    // Try to play a silent audio to register the user gesture for browser
    // autoplay. Failures are fine — the click itself is usually enough.
    try {
      const audio = new Audio(SILENT_MP3);
      const result = audio.play();
      if (result && typeof result.catch === 'function') {
        result.catch(() => {});
      }
    } catch (_) {
      // no-op
    }
    if (onStart) onStart();
  }

  return (
    <button type="button" onClick={handleClick} aria-label="Start voice session">
      🔊 Start voice session
    </button>
  );
}
```

### Step 4: Run — expect 3 tests pass

```bash
CI=true npm test -- --watchAll=false StartVoiceSessionButton
```

Expected: 3 passed.

### Step 5: Commit

```bash
git add src/components/StartVoiceSessionButton.js src/components/__tests__/StartVoiceSessionButton.test.js
git commit -m "[Feat] Add StartVoiceSessionButton component"
```

---

## Task 6: Frontend — mount button + gate AudioQueue enqueue in `App.js`

**Files:**
- Modify: `milo-front/src/App.js`

### Step 1: Add the state and the button mount

At the top of `App.js` imports (alongside `AudioQueue`):

```javascript
import StartVoiceSessionButton from './components/StartVoiceSessionButton';
```

Inside the `App()` function component, near the other `useState` declarations, add:

```javascript
  const [voiceSessionStarted, setVoiceSessionStarted] = useState(false);
```

### Step 2: Gate the audio enqueue in BOTH `openSession` callbacks

The two `apiClient.openSession?.(...)` call sites (initial + reconnect) both wire `onAudioSentence`. Update both to check the flag:

```javascript
        onAudioSentence: ({ data, mime }) => {
          if (voiceSessionStarted) {
            audioQueueRef.current.enqueue(data, mime);
          }
        },
```

(React's closure semantics: this reads `voiceSessionStarted` at call time. Since the callback is stable across the session, and `voiceSessionStarted` may flip mid-session, you need the closure to read fresh state. Easiest: use a ref. Add this near `audioQueueRef`:)

```javascript
  const voiceSessionStartedRef = useRef(false);
  useEffect(() => {
    voiceSessionStartedRef.current = voiceSessionStarted;
  }, [voiceSessionStarted]);
```

Then the callback becomes:

```javascript
        onAudioSentence: ({ data, mime }) => {
          if (voiceSessionStartedRef.current) {
            audioQueueRef.current.enqueue(data, mime);
          }
        },
```

### Step 3: Render the button somewhere visible in the chat layout

In the JSX section that renders the chat view (find where `<ChatComposer>` is rendered — it's likely inside a `<main>` or `<section>` near the message thread), add the button ABOVE the composer:

```javascript
        <StartVoiceSessionButton
          active={voiceSessionStarted}
          onStart={() => setVoiceSessionStarted(true)}
        />
```

When `voiceSessionStarted` is true the button auto-hides (per its `active` prop).

### Step 4: Run all frontend tests — no regressions

```bash
CI=true npm test -- --watchAll=false
```

Expected: all tests still pass.

### Step 5: Commit

```bash
git add src/App.js
git commit -m "[Feat] Gate audio playback behind StartVoiceSessionButton"
```

---

## Task 7: Frontend — barge-in wiring in `MicButton`

**Files:**
- Modify: `milo-front/src/components/MicButton.js`
- Modify: `milo-front/src/components/ChatComposer.js`
- Modify: `milo-front/src/App.js` (one prop)

`MicButton` should run an `onBeforeRecord` callback at the moment recording begins. `App.js` provides that callback, which sends `cancel_turn` and flushes the queue. `ChatComposer` is the middleman — it accepts `onBeforeRecord` as a prop and forwards it to `MicButton`.

### Step 1: Add the `onBeforeRecord` prop to MicButton

In `src/components/MicButton.js`, change the signature:

```javascript
export default function MicButton({ onTranscript, onBeforeRecord, disabled = false }) {
```

In the `handleClick` function, just before `await startRecording();` in the start-recording branch:

```javascript
    if (!isRecording) {
      if (onBeforeRecord) {
        try { onBeforeRecord(); } catch (_) {}
      }
      await startRecording();
      return;
    }
```

### Step 2: Forward the prop through ChatComposer

In `src/components/ChatComposer.js`, change the signature:

```javascript
export default function ChatComposer({ onSend, onBeforeRecord, sending = false }) {
```

In the JSX, pass it to `<MicButton>`:

```javascript
        <MicButton
          disabled={sending}
          onBeforeRecord={onBeforeRecord}
          onTranscript={(text) => setDraft((current) => (current ? `${current} ${text}` : text))}
        />
```

### Step 3: Provide the callback from App.js

In `src/App.js`, find the `<ChatComposer>` render site and add the new prop:

```javascript
        <ChatComposer
          onSend={handleSend}
          sending={sendingMessage}
          onBeforeRecord={() => {
            apiClient.cancelInflightTurn?.();
            audioQueueRef.current?.flush();
          }}
        />
```

### Step 4: Run frontend tests — confirm MicButton tests still pass

```bash
CI=true npm test -- --watchAll=false
```

Expected: all tests still pass. The MicButton tests don't pass `onBeforeRecord`, so the new optional prop defaults to undefined and the behavior is unchanged for the existing test paths.

### Step 5: Commit

```bash
git add src/components/MicButton.js src/components/ChatComposer.js src/App.js
git commit -m "[Feat] Wire mic click to send cancel_turn + flush audio (barge-in)"
```

---

## Task 8: Manual smoke test

**Files:** none — verification only.

### Step 1: Restart backend

From `milo-back-agent-orchestrator/`:

```bash
.venv/bin/python3.11 -m uvicorn src.main:app --reload --port 8000
```

Expected: clean startup, no tracebacks.

### Step 2: Restart frontend

From `milo-front/`:

```bash
npm start
```

### Step 3: Sign in, open an activity

### Step 4: Confirm "Start voice session" button is visible

Above the composer, you should see a "🔊 Start voice session" button. Until you click it, **no audio should play** for any agent turn — even though the backend is still sending `audio_sentence` frames.

### Step 5: Verify text-only mode before clicking

Send a typed message. Confirm:
- Milo's response streams as text ✅
- No audio plays ✅
- Backend log shows successful `audio_sentence` frames being emitted (they're just being dropped client-side)

### Step 6: Click "Start voice session"

The button hides. From now on, audio should play.

### Step 7: Send another typed message

Confirm Milo's response streams AND audio plays. (English voice if response is English, Spanish if Spanish.)

### Step 8: Barge-in test — interrupt mid-audio

Send a message that will get a multi-sentence response. As soon as Milo starts speaking, click 🎤 (the mic). Expected:
- Audio stops immediately (queue flushed).
- Backend log shows `cancel_turn received — cancelling in-flight turn` and `turn cancelled after N accumulated chars`.
- Frontend shows the partial response that streamed before cancel.
- Mic starts recording your new utterance.

### Step 9: Send the new spoken question

Release / re-click mic to stop recording. Transcript appears. Send. New turn proceeds normally with audio.

### Step 10: Check the console

No red errors. Acceptable: silent-error warnings if `play()` was rejected for a specific frame.

### Step 11: Done

If steps 4–9 all worked, PR 3 is shippable.

---

## Done criteria

PR 3 is shippable when:
- All backend tests pass, including the two new `test_session_cancel.py` tests.
- All frontend tests pass, including the three new `StartVoiceSessionButton.test.js` tests.
- Manual smoke (Task 8) passes — barge-in interrupts audio cleanly, "Start voice session" gates audio playback correctly.

---

## Follow-ups deferred from PR 3

These don't block PR 3 but are worth tracking:

- **Drop the plain-text WS frame fallback** in `_receive_frame`. Once all clients are confirmed to send JSON (post-PR 3 deploy), simplify the parser.
- **`sessionSocket` dispatch unit test** for `audio_sentence` (flagged in PR 2 final review, still open) — mock a fake WS that emits `audio_sentence` frames and assert `onAudioSentence` is invoked with the correct shape.
- **`audio_stream` integration test** with a fake LLM stream (flagged in PR 2 final review).
- **RAG `document_embeddings.activity_id` backfill** — flagged in [rag.py:51-56](../../src/services/rag.py#L51-L56). Re-ingest teacher documents tagging each row with its source activity so the activity-scoped branch returns useful results.
- **Cancel UX polish** — when the user barges in, the partial response still shows on screen. Consider visually marking it (greyed-out, "(interrupted)" tag) so the student knows what happened.
- **Race: barge-in fires while STT upload is in flight** — currently, clicking mic during STT upload calls `onBeforeRecord` (cancel + flush) but the STT upload is still in flight. The new recording starts; when STT resolves, the old transcript may still arrive in the composer. Worth fixing with an STT-upload cancellation token.
