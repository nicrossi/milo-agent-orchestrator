# Voice I/O — PR 1 (STT) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add push-to-talk speech-to-text so a student can speak their question in the chat composer and have the transcribed text dropped into the input box before they send it. No TTS yet — output stays as text. This is PR 1 of three from the design spec.

**Architecture:** New backend HTTP endpoint `POST /audio/transcribe` (auth-gated, 25 MB max upload) that forwards multipart audio to OpenAI Whisper via a thin async service wrapper. New frontend `useAudioCapture` hook (MediaRecorder API) and `<MicButton>` component integrated into the existing `<ChatComposer>`. No WebSocket changes; STT is intentionally one-shot HTTP per the spec (decision Q4 / Approach 2).

**Tech Stack:**
- Backend: FastAPI 0.135, Python async, `openai>=1.55` (upgrade from pinned 0.27 — unused), pytest, `httpx.AsyncClient` for endpoint tests.
- Frontend: Create React App (react-scripts 5), React 19, browser MediaRecorder API, React Testing Library + Jest.
- Spec: [docs/specs/2026-05-17-voice-io-design.md](../specs/2026-05-17-voice-io-design.md)

---

## File Structure

**Backend — create:**
- `src/services/stt.py` — Whisper async wrapper (`transcribe` + `STTError`).
- `src/schemas/audio.py` — Pydantic response model for the transcribe endpoint.
- `src/api/routers/audio.py` — `POST /audio/transcribe`.
- `tests/services/__init__.py`, `tests/services/test_stt.py` — STT service unit tests.
- `tests/api/__init__.py`, `tests/api/conftest.py`, `tests/api/test_audio_router.py` — endpoint tests.

**Backend — modify:**
- `requirements.txt` — bump `openai==0.27.10` → `openai==1.55.0`.
- `.env.example` — add `OPENAI_API_KEY`.
- `src/main.py` — register the new audio router.

**Frontend — create:**
- `src/hooks/useAudioCapture.js` — push-to-talk MediaRecorder hook.
- `src/components/MicButton.js` — push-to-talk UI with upload-on-stop.
- `src/components/__tests__/MicButton.test.js` — component tests.

**Frontend — modify:**
- `src/components/ChatComposer.js` — render the `<MicButton>` and accept transcript into the draft state.

**PR 1 deliberate simplifications (deferred to later PRs / follow-ups):**
- No `language_hint` from frontend (Whisper autodetect). Backend accepts it as `Optional[str]` for forward-compat. PR 2 wires the hint.
- No rate limiting middleware (auth + 25 MB cap suffice). Tracked in spec's "Open implementation questions".
- No streaming TTS — only the STT half of the loop.

---

## Task 1: Environment & dependency bump

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: Bump the OpenAI SDK pin in `requirements.txt`**

The current pin `openai==0.27.10` is the pre-November-2023 API and is unused in the codebase (verified via `grep`). Bump to a modern Whisper-API-compatible version.

Edit `requirements.txt`: change the line `openai==0.27.10` to `openai==1.55.0`.

- [ ] **Step 2: Add `OPENAI_API_KEY` to `.env.example`**

Append to `.env.example`:

```
OPENAI_API_KEY=""
```

Place it near the other API-key entries (just below `GOOGLE_API_KEY`).

- [ ] **Step 3: Install the new dependency locally**

Run: `pip install -r requirements.txt`

Expected: `openai-1.55.0` (or newer) installed without errors. No conflicts with the rest of the requirements (verified — `openai` 1.x has independent deps from the rest).

- [ ] **Step 4: Set a real `OPENAI_API_KEY` in `.env` for testing**

Edit `.env` (not `.env.example`) and add the real key. Required for the manual smoke test in Task 8.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .env.example
git commit -m "[Build] Bump openai SDK to 1.55 and add OPENAI_API_KEY env"
```

---

## Task 2: Pydantic schema for transcribe response

**Files:**
- Create: `src/schemas/audio.py`

- [ ] **Step 1: Create `src/schemas/audio.py`**

```python
from pydantic import BaseModel, Field


class TranscribeResponse(BaseModel):
    text: str = Field(..., description="Transcribed text from the uploaded audio.")
```

- [ ] **Step 2: Commit**

```bash
git add src/schemas/audio.py
git commit -m "[Feat] Add audio schema for transcribe response"
```

---

## Task 3: STT service wrapper (TDD)

**Files:**
- Create: `tests/services/__init__.py`
- Create: `tests/services/test_stt.py`
- Create: `src/services/stt.py`

- [ ] **Step 1: Create the empty test package marker**

Create `tests/services/__init__.py` (empty file).

- [ ] **Step 2: Write the failing happy-path test**

Create `tests/services/test_stt.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_transcribe_returns_text_from_whisper():
    from src.services.stt import transcribe

    fake_response = MagicMock()
    fake_response.text = "hola mundo"

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=fake_response)

    with patch("src.services.stt._get_client", return_value=mock_client):
        result = await transcribe(audio_bytes=b"\x00\x01\x02", mime="audio/webm")

    assert result == "hola mundo"
    mock_client.audio.transcriptions.create.assert_awaited_once()
    call_kwargs = mock_client.audio.transcriptions.create.await_args.kwargs
    assert call_kwargs["model"] == "whisper-1"
    assert call_kwargs["file"] == ("audio.webm", b"\x00\x01\x02", "audio/webm")
    assert "language" not in call_kwargs
```

- [ ] **Step 3: Run the test to verify it fails (ImportError)**

Run from the backend repo root:

```bash
pytest tests/services/test_stt.py::test_transcribe_returns_text_from_whisper -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.stt'` (or similar). If `pytest-asyncio` isn't auto-installed, also add it; the existing tests in `tests/policy/` already use async patterns, so it should be present. If missing: `pip install pytest-asyncio` and add to `requirements.txt`.

- [ ] **Step 4: Implement the minimal `services/stt.py`**

Create `src/services/stt.py`:

```python
import logging
import os
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger("milo-orchestrator.stt")


class STTError(Exception):
    """Raised when speech-to-text transcription fails terminally."""


_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise STTError("OPENAI_API_KEY is not configured.")
        _client = AsyncOpenAI(api_key=api_key)
    return _client


def _filename_for_mime(mime: str) -> str:
    if "webm" in mime:
        return "audio.webm"
    if "mp4" in mime or "m4a" in mime:
        return "audio.m4a"
    if "wav" in mime:
        return "audio.wav"
    if "mpeg" in mime or "mp3" in mime:
        return "audio.mp3"
    return "audio.bin"


async def transcribe(
    audio_bytes: bytes,
    mime: str,
    language_hint: Optional[str] = None,
) -> str:
    client = _get_client()
    filename = _filename_for_mime(mime)
    kwargs = {
        "model": "whisper-1",
        "file": (filename, audio_bytes, mime),
    }
    if language_hint:
        kwargs["language"] = language_hint

    response = await client.audio.transcriptions.create(**kwargs)
    return response.text
```

- [ ] **Step 5: Run the happy-path test — expect PASS**

```bash
pytest tests/services/test_stt.py::test_transcribe_returns_text_from_whisper -v
```

Expected: PASS.

- [ ] **Step 6: Add a failing test for `language_hint` propagation**

Append to `tests/services/test_stt.py`:

```python
@pytest.mark.asyncio
async def test_transcribe_passes_language_hint_when_given():
    from src.services.stt import transcribe

    fake_response = MagicMock()
    fake_response.text = "hola"

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=fake_response)

    with patch("src.services.stt._get_client", return_value=mock_client):
        await transcribe(audio_bytes=b"x", mime="audio/webm", language_hint="es")

    call_kwargs = mock_client.audio.transcriptions.create.await_args.kwargs
    assert call_kwargs["language"] == "es"
```

- [ ] **Step 7: Run — expect PASS already**

```bash
pytest tests/services/test_stt.py::test_transcribe_passes_language_hint_when_given -v
```

Expected: PASS (the implementation already handles this — confirms correctness, not new code).

- [ ] **Step 8: Add a failing test for `STTError` on Whisper API failure**

Append:

```python
@pytest.mark.asyncio
async def test_transcribe_raises_stt_error_on_api_failure():
    from src.services.stt import STTError, transcribe

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("src.services.stt._get_client", return_value=mock_client):
        with pytest.raises(STTError):
            await transcribe(audio_bytes=b"x", mime="audio/webm")
```

- [ ] **Step 9: Run — expect FAIL (raises `RuntimeError`, not `STTError`)**

```bash
pytest tests/services/test_stt.py::test_transcribe_raises_stt_error_on_api_failure -v
```

Expected: FAIL — `RuntimeError: boom` propagates instead of being wrapped.

- [ ] **Step 10: Wrap the API call with `STTError`**

Replace the body of `transcribe` in `src/services/stt.py` so the API call is wrapped:

```python
    try:
        response = await client.audio.transcriptions.create(**kwargs)
    except Exception as exc:
        logger.warning("Whisper transcription failed: %s", exc)
        raise STTError(str(exc)) from exc
    return response.text
```

- [ ] **Step 11: Run — expect PASS for all three tests**

```bash
pytest tests/services/test_stt.py -v
```

Expected: 3 passed.

- [ ] **Step 12: Commit**

```bash
git add tests/services/__init__.py tests/services/test_stt.py src/services/stt.py
git commit -m "[Feat] Add stt service wrapping OpenAI Whisper API"
```

---

## Task 4: Audio transcribe router (TDD)

**Files:**
- Create: `tests/api/__init__.py`
- Create: `tests/api/conftest.py`
- Create: `tests/api/test_audio_router.py`
- Create: `src/api/routers/audio.py`
- Modify: `src/main.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/api/__init__.py` (empty).

- [ ] **Step 2: Create `tests/api/conftest.py` with a FastAPI test client fixture**

```python
import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")


@pytest.fixture
async def client():
    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

This relies on the existing `AUTH_REQUIRED=false` short-circuit at [core/auth.py:87](../../src/core/auth.py#L87), which returns a `dev-user` instead of verifying Firebase. Lets endpoint tests run without a Firebase project.

- [ ] **Step 3: Write the failing happy-path test for the transcribe endpoint**

Create `tests/api/test_audio_router.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_transcribe_returns_text_on_success(client):
    with patch("src.api.routers.audio.stt.transcribe", new=AsyncMock(return_value="hola")):
        files = {"file": ("audio.webm", b"\x00\x01\x02", "audio/webm")}
        response = await client.post("/audio/transcribe", files=files)

    assert response.status_code == 200
    assert response.json() == {"text": "hola"}
```

- [ ] **Step 4: Run — expect 404 (route not registered yet)**

```bash
pytest tests/api/test_audio_router.py::test_transcribe_returns_text_on_success -v
```

Expected: FAIL — assertion `response.status_code == 200` fails (gets 404).

- [ ] **Step 5: Create `src/api/routers/audio.py`**

```python
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.core.auth import AuthenticatedUser, require_http_user
from src.schemas.audio import TranscribeResponse
from src.services import stt

logger = logging.getLogger("milo-orchestrator.audio")

router = APIRouter(prefix="/audio", tags=["Audio"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # Whisper API hard limit


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    language_hint: Optional[str] = Form(default=None),
    user: AuthenticatedUser = Depends(require_http_user),
) -> TranscribeResponse:
    audio_bytes = await file.read()
    if len(audio_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Audio exceeds {MAX_UPLOAD_BYTES} bytes.",
        )
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio upload.")

    mime = file.content_type or "audio/webm"

    try:
        text = await stt.transcribe(
            audio_bytes=audio_bytes,
            mime=mime,
            language_hint=language_hint,
        )
    except stt.STTError as exc:
        logger.warning("Transcribe failed for user=%s: %s", user.uid, exc)
        raise HTTPException(status_code=502, detail="Transcription service failed.")

    return TranscribeResponse(text=text)
```

- [ ] **Step 6: Register the router in `src/main.py`**

In `src/main.py`, find the imports block at line 9:

```python
from src.api.routers import activities, admin, chat, courses, me, policy, students
```

Replace with:

```python
from src.api.routers import activities, admin, audio, chat, courses, me, policy, students
```

Then find the `app.include_router(...)` block (lines 64–70) and append:

```python
app.include_router(audio.router)
```

(Order doesn't matter — placing it last keeps the diff focused.)

- [ ] **Step 7: Run the happy-path test — expect PASS**

```bash
pytest tests/api/test_audio_router.py::test_transcribe_returns_text_on_success -v
```

Expected: PASS.

- [ ] **Step 8: Add a failing test for 413 on oversize upload**

Append to `tests/api/test_audio_router.py`:

```python
@pytest.mark.asyncio
async def test_transcribe_rejects_oversize_upload(client):
    big_blob = b"x" * (26 * 1024 * 1024)  # 26 MB > 25 MB cap
    files = {"file": ("audio.webm", big_blob, "audio/webm")}
    response = await client.post("/audio/transcribe", files=files)
    assert response.status_code == 413
```

- [ ] **Step 9: Run — expect PASS (size check already in place)**

```bash
pytest tests/api/test_audio_router.py::test_transcribe_rejects_oversize_upload -v
```

Expected: PASS.

- [ ] **Step 10: Add a failing test for empty upload (400)**

Append:

```python
@pytest.mark.asyncio
async def test_transcribe_rejects_empty_upload(client):
    files = {"file": ("audio.webm", b"", "audio/webm")}
    response = await client.post("/audio/transcribe", files=files)
    assert response.status_code == 400
```

- [ ] **Step 11: Run — expect PASS**

```bash
pytest tests/api/test_audio_router.py::test_transcribe_rejects_empty_upload -v
```

Expected: PASS.

- [ ] **Step 12: Add a failing test for STT failure → 502**

Append:

```python
@pytest.mark.asyncio
async def test_transcribe_returns_502_when_stt_errors(client):
    from src.services.stt import STTError

    with patch(
        "src.api.routers.audio.stt.transcribe",
        new=AsyncMock(side_effect=STTError("whisper down")),
    ):
        files = {"file": ("audio.webm", b"\x00\x01", "audio/webm")}
        response = await client.post("/audio/transcribe", files=files)

    assert response.status_code == 502
```

- [ ] **Step 13: Run — expect PASS**

```bash
pytest tests/api/test_audio_router.py::test_transcribe_returns_502_when_stt_errors -v
```

Expected: PASS.

- [ ] **Step 14: Add a failing test for `language_hint` form field propagation**

Append:

```python
@pytest.mark.asyncio
async def test_transcribe_forwards_language_hint(client):
    captured = {}

    async def fake_transcribe(audio_bytes, mime, language_hint=None):
        captured["language_hint"] = language_hint
        return "hola"

    with patch("src.api.routers.audio.stt.transcribe", new=fake_transcribe):
        files = {"file": ("audio.webm", b"\x00", "audio/webm")}
        data = {"language_hint": "es"}
        response = await client.post("/audio/transcribe", files=files, data=data)

    assert response.status_code == 200
    assert captured["language_hint"] == "es"
```

- [ ] **Step 15: Run — expect PASS**

```bash
pytest tests/api/test_audio_router.py -v
```

Expected: 5 passed.

- [ ] **Step 16: Run the full test suite to confirm nothing else broke**

```bash
pytest -v
```

Expected: all existing tests still pass, plus the new ones.

- [ ] **Step 17: Commit**

```bash
git add tests/api/__init__.py tests/api/conftest.py tests/api/test_audio_router.py \
        src/api/routers/audio.py src/main.py
git commit -m "[Feat] Add POST /audio/transcribe endpoint for STT"
```

---

## Task 5: Frontend `useAudioCapture` hook

**Files:**
- Create: `milo-front/src/hooks/useAudioCapture.js`

> All frontend tasks run from `/Users/saints/Desktop/ITBA/PF/milo-front/`. The backend repo stays untouched during Tasks 5–7.

- [ ] **Step 1: Create the hook**

Create `src/hooks/useAudioCapture.js`:

```javascript
import { useCallback, useRef, useState } from 'react';

// Push-to-talk audio capture using the browser MediaRecorder API.
// Caller drives start/stop; the hook produces a single Blob per session.
export default function useAudioCapture() {
  const [isRecording, setIsRecording] = useState(false);
  const [error, setError] = useState(null);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);

  const startRecording = useCallback(async () => {
    if (mediaRecorderRef.current) return;
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch (err) {
      setError(err);
      setIsRecording(false);
    }
  }, []);

  const stopRecording = useCallback(() => {
    return new Promise((resolve) => {
      const recorder = mediaRecorderRef.current;
      if (!recorder) {
        resolve(null);
        return;
      }
      recorder.onstop = () => {
        const mime = recorder.mimeType || 'audio/webm';
        const blob = new Blob(chunksRef.current, { type: mime });
        chunksRef.current = [];
        mediaRecorderRef.current = null;
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((track) => track.stop());
          streamRef.current = null;
        }
        setIsRecording(false);
        resolve(blob);
      };
      recorder.stop();
    });
  }, []);

  return { isRecording, error, startRecording, stopRecording };
}
```

- [ ] **Step 2: Commit**

```bash
git add src/hooks/useAudioCapture.js
git commit -m "[Feat] Add useAudioCapture hook for push-to-talk recording"
```

---

## Task 6: Frontend `<MicButton>` component

**Files:**
- Create: `milo-front/src/components/MicButton.js`
- Create: `milo-front/src/components/__tests__/MicButton.test.js`

- [ ] **Step 1: Create the component**

Create `src/components/MicButton.js`:

```javascript
import { useState } from 'react';
import { auth } from '../firebase';
import useAudioCapture from '../hooks/useAudioCapture';

const API_BASE = process.env.REACT_APP_API_BASE || 'http://localhost:8000';

export default function MicButton({ onTranscript, disabled = false }) {
  const { isRecording, startRecording, stopRecording } = useAudioCapture();
  const [isUploading, setIsUploading] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  async function handleClick() {
    if (disabled || isUploading) return;
    if (!isRecording) {
      await startRecording();
      return;
    }
    const blob = await stopRecording();
    if (!blob || blob.size === 0) return;
    setIsUploading(true);
    setErrorMessage(null);
    try {
      const form = new FormData();
      form.append('file', blob, 'audio.webm');
      const token = auth?.currentUser ? await auth.currentUser.getIdToken() : '';
      const response = await fetch(`${API_BASE}/audio/transcribe`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
      if (!response.ok) {
        throw new Error(`Transcribe failed: ${response.status}`);
      }
      const data = await response.json();
      if (data.text && onTranscript) {
        onTranscript(data.text);
      }
    } catch (err) {
      setErrorMessage("Couldn't transcribe — try again.");
    } finally {
      setIsUploading(false);
    }
  }

  let label;
  if (isUploading) label = 'Transcribing…';
  else if (isRecording) label = 'Stop';
  else label = '🎤';

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled || isUploading}
      aria-label={isRecording ? 'Stop recording' : 'Start recording'}
      title={errorMessage || (isRecording ? 'Click to stop and send' : 'Click to record')}
    >
      {label}
    </button>
  );
}
```

Notes:
- The `API_BASE` falls back to `http://localhost:8000` (matches the backend dev port). If the project already exposes an env or constant for the base URL, use it instead — check `src/api/sessionSocket.js` for an existing pattern before merging.
- Uses Firebase ID token from the existing `src/firebase` module — same auth path as other authenticated requests in this app.

- [ ] **Step 2: Verify `auth` export path matches existing code**

Confirm that `src/firebase/index.js` (or equivalent) exports a Firebase `auth` object. If the export is different (e.g., `import auth from '../firebase/auth'`), update the import in `MicButton.js` to match the project convention used by other authenticated fetch calls. Search for `currentUser.getIdToken` in the existing `src/api/` files to find the canonical pattern.

- [ ] **Step 3: Write component tests**

Create `src/components/__tests__/MicButton.test.js`:

```javascript
import { render, screen } from '@testing-library/react';
import MicButton from '../MicButton';

jest.mock('../../hooks/useAudioCapture', () => () => ({
  isRecording: false,
  startRecording: jest.fn(),
  stopRecording: jest.fn(),
  error: null,
}));

jest.mock('../../firebase', () => ({
  auth: { currentUser: null },
}));

describe('MicButton', () => {
  test('renders the mic glyph when idle', () => {
    render(<MicButton onTranscript={() => {}} />);
    expect(screen.getByRole('button', { name: /start recording/i })).toBeInTheDocument();
  });

  test('respects the disabled prop', () => {
    render(<MicButton onTranscript={() => {}} disabled />);
    expect(screen.getByRole('button')).toBeDisabled();
  });
});
```

- [ ] **Step 4: Run frontend tests**

From `milo-front/`:

```bash
npm test -- --watchAll=false MicButton
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/components/MicButton.js src/components/__tests__/MicButton.test.js
git commit -m "[Feat] Add MicButton push-to-talk component"
```

---

## Task 7: Integrate `<MicButton>` into `<ChatComposer>`

**Files:**
- Modify: `milo-front/src/components/ChatComposer.js`

- [ ] **Step 1: Edit `ChatComposer.js` to render `<MicButton>` and accept transcripts into the draft**

Open `src/components/ChatComposer.js`. Change the imports at the top from:

```javascript
import { useState } from 'react';
```

to:

```javascript
import { useState } from 'react';
import MicButton from './MicButton';
```

Inside the form (between the `<input>` and the `<label className="attach-button">`), add:

```javascript
        <MicButton
          disabled={sending}
          onTranscript={(text) => setDraft((current) => (current ? `${current} ${text}` : text))}
        />
```

So the JSX block becomes:

```javascript
      <div className="composer-row reflection-composer-row">
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Type your response..."
        />
        <MicButton
          disabled={sending}
          onTranscript={(text) => setDraft((current) => (current ? `${current} ${text}` : text))}
        />
        <label className="attach-button" aria-disabled={sending}>
```

Rationale for the `onTranscript` behavior: the transcript is appended (not replaced) so a student can dictate a clarification on top of typed text. This matches the "transcript appears in input box, sends as text" behavior described in the spec's PR 1 rollout.

- [ ] **Step 2: Run all frontend tests**

From `milo-front/`:

```bash
npm test -- --watchAll=false
```

Expected: all existing tests still pass, plus the two new MicButton tests. ChatComposer tests (if any) still pass; the addition is non-breaking because `<MicButton>` is purely additive UI.

- [ ] **Step 3: Commit**

```bash
git add src/components/ChatComposer.js
git commit -m "[Feat] Wire MicButton into ChatComposer"
```

---

## Task 8: Manual smoke test (full loop, in a browser)

**Files:** none — verification only.

- [ ] **Step 1: Start the backend**

From `milo-back-agent-orchestrator/`:

```bash
uvicorn src.main:app --reload --port 8000
```

Expected: server starts, logs show `RAG service ready`, `Database ready`, etc. No tracebacks. The `/audio/transcribe` route appears in the FastAPI auto-generated `/docs` page at <http://localhost:8000/docs>.

- [ ] **Step 2: Start the frontend**

From `milo-front/`:

```bash
npm start
```

Expected: dev server starts on port 3000, browser opens.

- [ ] **Step 3: Sign in and open a chat activity**

Use your existing dev login flow. Navigate to any chat activity where the `<ChatComposer>` is visible.

- [ ] **Step 4: Test the happy path — English**

Click the 🎤 button. Browser prompts for microphone permission — allow it. Speak in English ("What is the capital of France?"), click the button again (now showing "Stop"). The button briefly shows "Transcribing…", then the transcribed text appears in the input box. Click Send.

Expected: transcript is reasonable (Whisper is high-accuracy at 10+ second clear utterances), it appears in the input, and the existing send flow works unchanged.

- [ ] **Step 5: Test the happy path — Spanish**

Same flow, speak in Spanish ("¿Cuál es la capital de Francia?"). Expected: Spanish transcript appears (autodetect works).

- [ ] **Step 6: Test failure handling — backend offline**

Stop the backend (`Ctrl-C` in its terminal). Record again. Expected: button finishes the upload attempt, shows the `errorMessage` title tooltip ("Couldn't transcribe — try again."), draft is unchanged, no crash.

Restart the backend before continuing.

- [ ] **Step 7: Test mic permission denied**

In Chrome devtools: Settings → Privacy → Site settings → revoke microphone for `localhost:3000`. Click 🎤. Expected: `getUserMedia` rejects, the hook sets `error`, the button stops being in recording state, no crash. Re-grant permission and re-test.

- [ ] **Step 8: Confirm there are no console errors**

Open the browser devtools console. Expected: no red errors during the full flow.

- [ ] **Step 9: Final task done — no commit needed (smoke test only)**

If anything in Steps 4–8 fails, fix it before declaring PR 1 complete. Note the failure in the PR description.

---

## Done criteria

PR 1 is shippable when:
- All backend tests in `tests/services/test_stt.py` and `tests/api/test_audio_router.py` pass alongside the existing suite.
- All frontend tests pass (`npm test -- --watchAll=false`).
- Manual smoke test (Task 8) passes in both English and Spanish.
- No regressions: the existing chat flow (typed text → agent response) still works with no behavior change.

---

## Follow-ups deferred from PR 1

These are intentionally out of scope and tracked here so PR 2 / PR 3 / a follow-up small-PR can pick them up:

- **Rate limiting** on `POST /audio/transcribe` (per-user token bucket). Auth + 25 MB cap protect us short-term; revisit when voice traffic ramps up.
- **`language_hint` from frontend** — frontend always sends without a hint right now; PR 2 will derive it from the last assistant message and pass it as a form field.
- **Streaming TTS** — entire PR 2 scope; emits `audio_sentence` WS frames during agent responses.
- **Voice session UX glue** — PR 3 adds `<StartVoiceSessionButton>`, barge-in via `cancel_turn` WS frame, and the `<AudioQueuePlayer>` with `flushQueue()`.
