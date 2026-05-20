# Voice I/O — PR 2 (TTS sentence-streaming) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream the agent's response as both text chunks *and* MP3 audio sentences over the existing WebSocket so the student hears Milo speak while reading. Adds EdgeTTS synthesis, sentence-by-sentence boundary detection on the live LLM stream, per-turn voice locking (one Spanish voice + one English voice), and a frontend audio queue that plays sentences sequentially.

**Architecture:** A new `src/api/audio_stream.py` wraps the LLM's `AsyncIterator[str]` and yields a mixed stream of `TextChunk` and `AudioSentence` events. As tokens arrive, sentence boundaries are detected (reusing `split_sentences()` from the policy interceptor). Each completed sentence is synthesized via `services/tts.py` (EdgeTTS, async, 1 retry on failure) and emitted as an `AudioSentence`. Voice is locked on the first sentence based on a language heuristic. `session.py` iterates the wrapped stream and emits two WebSocket frame types: existing `chunk` for text and a new `audio_sentence` (base64 MP3) for audio. After the LLM stream ends, the policy interceptor's correction (if any) is synthesized as one final audio sentence — same voice lock. On the frontend, a small `AudioQueue` class consumes `audio_sentence` frames, decodes the base64 blob to a Blob URL, and plays sentences sequentially via `<audio>` elements.

**Tech Stack:**
- Backend: FastAPI, Python async, `edge-tts` (free Microsoft TTS, no API key), pytest, pytest-asyncio.
- Frontend: React 19, Web Audio API (via `new Audio(blobUrl)`).
- Spec: [docs/specs/2026-05-17-voice-io-design.md](../specs/2026-05-17-voice-io-design.md)
- Prior PR: PR 1 (STT) — branch `feat/voice-io-pr1-stt`.

---

## File Structure

**Backend — create:**
- `src/services/tts.py` — EdgeTTS async wrapper with retry (`synthesize`, `TTSError`).
- `src/api/audio_stream.py` — wraps an `AsyncIterator[str]`, yields `TextChunk | AudioSentence`. Owns the streaming sentence extractor, voice lock, and language heuristic.
- `tests/services/test_tts.py` — TTS wrapper unit tests.
- `tests/api/test_audio_stream.py` — audio_stream wrapper unit tests.
- `milo-front/src/audio/audioQueue.js` — plain ES module class managing the playback queue (no React).
- `milo-front/src/audio/__tests__/audioQueue.test.js` — class-level unit tests.

**Backend — modify:**
- `requirements.txt` — add `edge-tts==7.0.0`.
- `.env.example` — add `EDGE_TTS_VOICE_ES`, `EDGE_TTS_VOICE_EN`.
- `src/api/session.py` (around [line 382-399](../../src/api/session.py#L382)) — replace the LLM stream loop with iteration over `audio_stream.wrap(...)`. Emit `audio_sentence` frames. After the policy interceptor fires (line 393-396), synthesize the correction and emit one trailing `audio_sentence`.

**Frontend — modify:**
- `milo-front/src/api/sessionSocket.js` — accept an `onAudioSentence` callback alongside the existing `onToken`; dispatch when `msg.type === "audio_sentence"`.
- One mount point (probably [milo-front/src/pages/](../../../milo-front/src/pages/) — the page that constructs the `SessionSocket`) — wire `onAudioSentence` to call `audioQueue.enqueue(base64, mime)`. Implementer locates the existing `SessionSocket` instantiation and adds the new callback prop.

**PR 2 deliberate simplifications (deferred to PR 3 or later):**
- No barge-in (`cancel_turn` WS frame, mic interruption) — student cannot cut Milo off mid-audio; mic is disabled by parent component while audio is playing if desired.
- No `<StartVoiceSessionButton>` autoplay-unlock — relies on existing user gesture (the previous mic click or send) to satisfy browser autoplay policy.
- No language hint propagation from frontend to STT (PR 1 already accepts it server-side but frontend still doesn't send it; PR 2 doesn't change that).
- No persistence of audio (per spec Q6, ephemeral).
- Sequential TTS synth (not concurrent). One sentence's TTS blocks the next LLM token by ~1-2 s, but only at sentence boundaries — acceptable for v2.

---

## Task 1: Add edge-tts dependency and voice env config

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: Add `edge-tts` to `requirements.txt`**

Append to `requirements.txt` (alphabetical placement: between `distro` and `executing`, or wherever consistent with surrounding entries):

```
edge-tts==7.0.0
```

- [ ] **Step 2: Install into the project venv**

```bash
.venv/bin/python3.11 -m pip install -r requirements.txt 2>&1 | tail -5
```

Expected: `Successfully installed edge-tts-7.0.0` (or already-installed line if previously installed). Then verify:

```bash
.venv/bin/python3.11 -c "import edge_tts; print('edge-tts:', edge_tts.__version__)"
```

Expected: `edge-tts: 7.0.0` (or newer).

- [ ] **Step 3: Add voice env vars to `.env.example`**

Append (near `OPENAI_API_KEY`):

```
EDGE_TTS_VOICE_ES="es-MX-DaliaNeural"
EDGE_TTS_VOICE_EN="en-US-AriaNeural"
```

These are EdgeTTS voice IDs. `es-MX-DaliaNeural` is a neutral Latin American Spanish voice; `en-US-AriaNeural` is a US English voice. Both are free and don't require an API key.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .env.example
git commit -m "[Build] Add edge-tts dependency and voice env vars"
```

---

## Task 2: TTS service wrapper (TDD)

**Files:**
- Create: `src/services/tts.py`
- Create: `tests/services/test_tts.py`

- [ ] **Step 1: Write the failing happy-path test**

Create `tests/services/test_tts.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_synthesize_returns_concatenated_audio_bytes():
    from src.services.tts import synthesize

    async def fake_stream():
        yield {"type": "audio", "data": b"\x00\x01"}
        yield {"type": "WordBoundary"}  # non-audio chunks ignored
        yield {"type": "audio", "data": b"\x02\x03"}

    mock_communicate = MagicMock()
    mock_communicate.stream = MagicMock(return_value=fake_stream())

    with patch("src.services.tts.edge_tts.Communicate", return_value=mock_communicate):
        result = await synthesize("hola mundo", "es-MX-DaliaNeural")

    assert result == b"\x00\x01\x02\x03"
```

- [ ] **Step 2: Run — expect FAIL (ImportError)**

```bash
.venv/bin/python3.11 -m pytest tests/services/test_tts.py::test_synthesize_returns_concatenated_audio_bytes -v
```

Expected: `ModuleNotFoundError: No module named 'src.services.tts'`.

- [ ] **Step 3: Create `src/services/tts.py` — minimal happy path**

```python
import asyncio
import logging
from typing import Optional

import edge_tts

logger = logging.getLogger("milo-orchestrator.tts")


class TTSError(Exception):
    """Raised when text-to-speech synthesis fails terminally."""


_RETRY_TIMEOUT_S = 0.5


async def synthesize(text: str, voice: str) -> bytes:
    """Synthesize `text` with EdgeTTS voice `voice`. Returns MP3 bytes.

    On a single transient failure, retries once with a ~500 ms timeout.
    Raises TTSError on terminal failure.
    """
    return await _synthesize_once(text, voice)


async def _synthesize_once(text: str, voice: str) -> bytes:
    communicate = edge_tts.Communicate(text, voice)
    chunks: list[bytes] = []
    async for event in communicate.stream():
        if event.get("type") == "audio":
            data = event.get("data")
            if data:
                chunks.append(data)
    return b"".join(chunks)
```

- [ ] **Step 4: Run — expect PASS**

```bash
.venv/bin/python3.11 -m pytest tests/services/test_tts.py::test_synthesize_returns_concatenated_audio_bytes -v
```

Expected: PASS.

- [ ] **Step 5: Add a failing test for "retry once on transient failure"**

Append to `tests/services/test_tts.py`:

```python
@pytest.mark.asyncio
async def test_synthesize_retries_once_on_transient_failure():
    from src.services.tts import synthesize

    call_count = {"value": 0}

    def factory(*args, **kwargs):
        call_count["value"] += 1
        mock = MagicMock()
        if call_count["value"] == 1:
            async def bad_stream():
                raise RuntimeError("flaky")
                yield  # unreachable, makes this an async generator
            mock.stream = MagicMock(return_value=bad_stream())
        else:
            async def good_stream():
                yield {"type": "audio", "data": b"OK"}
            mock.stream = MagicMock(return_value=good_stream())
        return mock

    with patch("src.services.tts.edge_tts.Communicate", side_effect=factory):
        result = await synthesize("hi", "en-US-AriaNeural")

    assert result == b"OK"
    assert call_count["value"] == 2  # one failure + one retry
```

- [ ] **Step 6: Run — expect FAIL (no retry implemented yet)**

```bash
.venv/bin/python3.11 -m pytest tests/services/test_tts.py::test_synthesize_retries_once_on_transient_failure -v
```

Expected: FAIL with `RuntimeError: flaky`.

- [ ] **Step 7: Implement retry in `synthesize`**

Replace the body of `synthesize` in `src/services/tts.py`:

```python
async def synthesize(text: str, voice: str) -> bytes:
    """Synthesize `text` with EdgeTTS voice `voice`. Returns MP3 bytes.

    On a single transient failure, retries once with a ~500 ms timeout.
    Raises TTSError on terminal failure.
    """
    try:
        return await asyncio.wait_for(_synthesize_once(text, voice), timeout=_RETRY_TIMEOUT_S + 10.0)
    except Exception as first_exc:
        logger.warning("EdgeTTS first attempt failed: %s — retrying", first_exc)
        try:
            return await asyncio.wait_for(_synthesize_once(text, voice), timeout=_RETRY_TIMEOUT_S + 10.0)
        except Exception as second_exc:
            logger.warning("EdgeTTS retry also failed: %s", second_exc)
            raise TTSError(str(second_exc)) from second_exc
```

Note on the `+ 10.0`: the spec's "~500 ms retry budget" is the delay budget for the *retry decision*, not for the underlying synth. Real EdgeTTS calls take 0.5-2 s on the wire. We give each attempt a generous 10.5 s timeout so a slow but successful call doesn't get killed; the retry is for failure modes (network blip, voice unavailable), not for slow-but-OK responses.

- [ ] **Step 8: Run — expect PASS**

```bash
.venv/bin/python3.11 -m pytest tests/services/test_tts.py::test_synthesize_retries_once_on_transient_failure -v
```

Expected: PASS.

- [ ] **Step 9: Add a failing test for "terminal failure → TTSError"**

Append:

```python
@pytest.mark.asyncio
async def test_synthesize_raises_tts_error_on_terminal_failure():
    from src.services.tts import TTSError, synthesize

    def factory(*args, **kwargs):
        mock = MagicMock()

        async def bad_stream():
            raise RuntimeError("permanent")
            yield

        mock.stream = MagicMock(return_value=bad_stream())
        return mock

    with patch("src.services.tts.edge_tts.Communicate", side_effect=factory):
        with pytest.raises(TTSError):
            await synthesize("hi", "en-US-AriaNeural")
```

- [ ] **Step 10: Run all TTS tests — expect 3 passed**

```bash
.venv/bin/python3.11 -m pytest tests/services/test_tts.py -v
```

Expected: 3 passed.

- [ ] **Step 11: Commit**

```bash
git add src/services/tts.py tests/services/test_tts.py
git commit -m "[Feat] Add tts service wrapping EdgeTTS with retry"
```

---

## Task 3: `audio_stream` wrapper (TDD)

**Files:**
- Create: `src/api/audio_stream.py`
- Create: `tests/api/test_audio_stream.py`

The wrapper iterates over an `AsyncIterator[str]` (the LLM token stream) and emits a mixed stream of `TextChunk` and `AudioSentence` events. It owns:
- Streaming sentence extraction using the existing `split_sentences()` plus a "wait for a terminator before emitting" guard.
- Voice lock based on a regex language heuristic on the first sentence.
- Per-sentence TTS calls — if TTS fails, the audio is skipped silently but the text already emitted is unaffected.

- [ ] **Step 1: Write the failing test for `TextChunk` emission**

Create `tests/api/test_audio_stream.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest


async def _gen(items):
    for it in items:
        yield it


@pytest.mark.asyncio
async def test_wrap_yields_text_chunks_for_every_llm_chunk():
    from src.api.audio_stream import TextChunk, wrap

    with patch("src.api.audio_stream.tts.synthesize", new=AsyncMock(return_value=b"")):
        events = []
        async for ev in wrap(_gen(["Hello ", "world", "."])):
            events.append(ev)

    text_chunks = [e for e in events if isinstance(e, TextChunk)]
    assert [c.text for c in text_chunks] == ["Hello ", "world", "."]
```

- [ ] **Step 2: Run — expect ImportError**

```bash
.venv/bin/python3.11 -m pytest tests/api/test_audio_stream.py::test_wrap_yields_text_chunks_for_every_llm_chunk -v
```

Expected: `ModuleNotFoundError: No module named 'src.api.audio_stream'`.

- [ ] **Step 3: Create the minimal `src/api/audio_stream.py`**

```python
import logging
import os
import re
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Union

from src.policy.interceptors.open_endedness_classifier import split_sentences
from src.services import tts

logger = logging.getLogger("milo-orchestrator.audio_stream")


@dataclass(frozen=True)
class TextChunk:
    text: str


@dataclass(frozen=True)
class AudioSentence:
    seq: int
    mp3_bytes: bytes
    voice: str


AudioStreamEvent = Union[TextChunk, AudioSentence]


_VOICE_ES_DEFAULT = "es-MX-DaliaNeural"
_VOICE_EN_DEFAULT = "en-US-AriaNeural"


def voice_for_language(language: str) -> str:
    if language == "es":
        return os.getenv("EDGE_TTS_VOICE_ES", _VOICE_ES_DEFAULT)
    return os.getenv("EDGE_TTS_VOICE_EN", _VOICE_EN_DEFAULT)


_SPANISH_MARKERS = re.compile(
    r"[¿¡]|\b("
    r"qué|está|por|para|con|los|las|una|este|esto|tú|sí|también|porque|pero|"
    r"cómo|cuándo|dónde|hola|gracias|hacer|hacia|puedes|puedo|hola|sobre"
    r")\b",
    re.IGNORECASE,
)


def detect_language(text: str) -> str:
    """Cheap heuristic: Spanish if inverted punctuation or common Spanish
    stopwords appear in the first ~120 chars. Otherwise English.
    """
    sample = text[:120]
    if _SPANISH_MARKERS.search(sample):
        return "es"
    return "en"


_TERMINATOR_RE = re.compile(r"[\?\!\.](?:\s|$)")


def _extract_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """Return (complete_sentences_in_order, remainder).

    A sentence is 'complete' only if followed by whitespace or end-of-string
    in the current buffer — we must not flush a partial last sentence on
    every keystroke.
    """
    last = None
    for m in _TERMINATOR_RE.finditer(buffer):
        last = m
    if last is None:
        return [], buffer
    split_point = last.start() + 1  # include the terminator
    complete_text = buffer[:split_point]
    remainder = buffer[split_point:]
    sentences = split_sentences(complete_text)
    return sentences, remainder


async def wrap(
    llm_stream: AsyncIterator[str],
) -> AsyncIterator[AudioStreamEvent]:
    buffer = ""
    voice: Optional[str] = None
    seq = 0

    async for chunk in llm_stream:
        yield TextChunk(chunk)
        buffer += chunk
        sentences, buffer = _extract_complete_sentences(buffer)
        for sentence in sentences:
            if voice is None:
                voice = voice_for_language(detect_language(sentence))
            try:
                mp3 = await tts.synthesize(sentence, voice)
                yield AudioSentence(seq=seq, mp3_bytes=mp3, voice=voice)
            except tts.TTSError as exc:
                logger.info("TTS failed for sentence (silent degrade): %s", exc)
            seq += 1

    # Flush remaining buffer as one final sentence
    leftover = buffer.strip()
    if leftover:
        if voice is None:
            voice = voice_for_language(detect_language(leftover))
        try:
            mp3 = await tts.synthesize(leftover, voice)
            yield AudioSentence(seq=seq, mp3_bytes=mp3, voice=voice)
        except tts.TTSError as exc:
            logger.info("TTS failed for tail sentence (silent degrade): %s", exc)
```

- [ ] **Step 4: Run — expect PASS**

```bash
.venv/bin/python3.11 -m pytest tests/api/test_audio_stream.py::test_wrap_yields_text_chunks_for_every_llm_chunk -v
```

Expected: PASS.

- [ ] **Step 5: Add a failing test for `AudioSentence` emission**

Append to `tests/api/test_audio_stream.py`:

```python
@pytest.mark.asyncio
async def test_wrap_emits_audio_sentence_after_terminator():
    from src.api.audio_stream import AudioSentence, wrap

    captured_calls = []

    async def fake_synth(text, voice):
        captured_calls.append((text, voice))
        return b"MP3"

    with patch("src.api.audio_stream.tts.synthesize", new=fake_synth):
        events = []
        async for ev in wrap(_gen(["Hello", " world", ". ", "Bye", "."])):
            events.append(ev)

    audio_events = [e for e in events if isinstance(e, AudioSentence)]
    assert len(audio_events) == 2
    assert audio_events[0].seq == 0
    assert audio_events[0].mp3_bytes == b"MP3"
    assert audio_events[1].seq == 1
    sentences_synthed = [c[0] for c in captured_calls]
    assert sentences_synthed == ["Hello world.", "Bye."]
```

- [ ] **Step 6: Run — expect PASS**

```bash
.venv/bin/python3.11 -m pytest tests/api/test_audio_stream.py::test_wrap_emits_audio_sentence_after_terminator -v
```

Expected: PASS.

- [ ] **Step 7: Add a failing test for voice lock**

Append:

```python
@pytest.mark.asyncio
async def test_wrap_locks_voice_to_first_sentence_language():
    from src.api.audio_stream import AudioSentence, wrap

    async def fake_synth(text, voice):
        return b"x"

    with patch("src.api.audio_stream.tts.synthesize", new=fake_synth):
        events = []
        # First sentence Spanish, second English — voice must stay Spanish.
        async for ev in wrap(_gen(["¿Cómo estás? ", "I am fine."])):
            events.append(ev)

    audio_events = [e for e in events if isinstance(e, AudioSentence)]
    assert len(audio_events) == 2
    voices = {e.voice for e in audio_events}
    assert len(voices) == 1
    assert next(iter(voices)).startswith("es-")
```

- [ ] **Step 8: Run — expect PASS**

```bash
.venv/bin/python3.11 -m pytest tests/api/test_audio_stream.py::test_wrap_locks_voice_to_first_sentence_language -v
```

Expected: PASS.

- [ ] **Step 9: Add a failing test for "TTS failure → silent skip"**

Append:

```python
@pytest.mark.asyncio
async def test_wrap_skips_audio_on_tts_failure_but_keeps_text():
    from src.api.audio_stream import AudioSentence, TextChunk, wrap
    from src.services.tts import TTSError

    async def flaky_synth(text, voice):
        if "boom" in text:
            raise TTSError("boom")
        return b"OK"

    with patch("src.api.audio_stream.tts.synthesize", new=flaky_synth):
        events = []
        async for ev in wrap(_gen(["Good. ", "boom. ", "Fine."])):
            events.append(ev)

    text_events = [e for e in events if isinstance(e, TextChunk)]
    audio_events = [e for e in events if isinstance(e, AudioSentence)]
    assert "".join(c.text for c in text_events) == "Good. boom. Fine."
    # Two of the three sentences succeed; the 'boom' one is silent
    assert len(audio_events) == 2
```

- [ ] **Step 10: Run — expect PASS**

```bash
.venv/bin/python3.11 -m pytest tests/api/test_audio_stream.py::test_wrap_skips_audio_on_tts_failure_but_keeps_text -v
```

Expected: PASS.

- [ ] **Step 11: Add a test for buffer-flush at end of stream (no terminator on last chunk)**

Append:

```python
@pytest.mark.asyncio
async def test_wrap_flushes_partial_buffer_at_stream_end():
    from src.api.audio_stream import AudioSentence, wrap

    captured = []

    async def fake_synth(text, voice):
        captured.append(text)
        return b"x"

    with patch("src.api.audio_stream.tts.synthesize", new=fake_synth):
        events = []
        async for ev in wrap(_gen(["Hola ", "sin punto final"])):
            events.append(ev)

    audio_events = [e for e in events if isinstance(e, AudioSentence)]
    assert len(audio_events) == 1
    assert captured == ["Hola sin punto final"]
```

- [ ] **Step 12: Run — expect PASS**

```bash
.venv/bin/python3.11 -m pytest tests/api/test_audio_stream.py -v
```

Expected: 5 passed.

- [ ] **Step 13: Commit**

```bash
git add src/api/audio_stream.py tests/api/test_audio_stream.py
git commit -m "[Feat] Add audio_stream wrapper for sentence-streamed TTS"
```

---

## Task 4: Wire `audio_stream` into `session.py`

**Files:**
- Modify: `src/api/session.py` around lines 374-399 (the LLM stream loop + interceptor block)

- [ ] **Step 1: Read the current loop to confirm exact line context**

Open `src/api/session.py` and locate the block:

```python
            # Step 3: stream with directives injected
            accumulated: List[str] = []
            async with get_db_session() as db:
                stream = self._agent.process_session_stream(
                    db, self._user_id, self._session_id, user_text,
                    self._context_description, self._activity_id,
                    prompt_directives=prompt_directives,
                )
                async for chunk in stream:
                    accumulated.append(chunk)
                    if not await self._send_json({"type": "chunk", "text": chunk}):
                        logger.info(
                            "Session '%s': client dropped mid-stream - halting.", self._session_id
                        )
                        return

            # Step 4: output interception
            if decision is not None:
                full_response = "".join(accumulated)
                was_intercepted, final_text = _policy_engine.check_output(full_response, decision)
                if was_intercepted:
                    correction = final_text[len(full_response):]
                    await self._send_json({"type": "chunk", "text": correction})
```

If the line numbers have shifted, find by searching for `"type": "chunk", "text": chunk`. The structure above is what to modify.

- [ ] **Step 2: Add imports at the top of `session.py`**

Find the existing import block and add:

```python
import base64

from src.api import audio_stream
from src.services import tts
```

(Place `import base64` with the other stdlib imports; the `src.api.audio_stream` and `src.services.tts` imports go with the other `src.*` imports.)

- [ ] **Step 3: Replace the stream loop**

Replace this block:

```python
                async for chunk in stream:
                    accumulated.append(chunk)
                    if not await self._send_json({"type": "chunk", "text": chunk}):
                        logger.info(
                            "Session '%s': client dropped mid-stream - halting.", self._session_id
                        )
                        return
```

with:

```python
                last_voice: str | None = None
                last_audio_seq: int = -1
                async for event in audio_stream.wrap(stream):
                    if isinstance(event, audio_stream.TextChunk):
                        accumulated.append(event.text)
                        if not await self._send_json({"type": "chunk", "text": event.text}):
                            logger.info(
                                "Session '%s': client dropped mid-stream - halting.", self._session_id
                            )
                            return
                    elif isinstance(event, audio_stream.AudioSentence):
                        last_voice = event.voice
                        last_audio_seq = event.seq
                        frame = {
                            "type": "audio_sentence",
                            "seq": event.seq,
                            "mime": "audio/mp3",
                            "voice": event.voice,
                            "data": base64.b64encode(event.mp3_bytes).decode("ascii"),
                        }
                        if not await self._send_json(frame):
                            logger.info(
                                "Session '%s': client dropped during audio - halting.", self._session_id
                            )
                            return
```

(If your Python version is < 3.10, change `str | None` to `Optional[str]` and add `from typing import Optional` if needed. The session file's other type hints will indicate which style to match.)

- [ ] **Step 4: Synthesize the interceptor correction as a final `audio_sentence`**

In the same file, find the existing interceptor block:

```python
                if was_intercepted:
                    correction = final_text[len(full_response):]
                    await self._send_json({"type": "chunk", "text": correction})
                    logger.info(
                        "Session '%s': interceptor fired — correction appended.", self._session_id
                    )
```

Replace it with:

```python
                if was_intercepted:
                    correction = final_text[len(full_response):]
                    await self._send_json({"type": "chunk", "text": correction})
                    logger.info(
                        "Session '%s': interceptor fired — correction appended.", self._session_id
                    )
                    # Q7: speak the correction in the same voice as the rest of the turn.
                    voice_for_correction = last_voice or audio_stream.voice_for_language(
                        audio_stream.detect_language(correction)
                    )
                    try:
                        correction_mp3 = await tts.synthesize(correction, voice_for_correction)
                        await self._send_json({
                            "type": "audio_sentence",
                            "seq": last_audio_seq + 1,
                            "mime": "audio/mp3",
                            "voice": voice_for_correction,
                            "data": base64.b64encode(correction_mp3).decode("ascii"),
                        })
                    except tts.TTSError as exc:
                        logger.info(
                            "Session '%s': correction TTS failed (silent degrade): %s",
                            self._session_id, exc,
                        )
```

- [ ] **Step 5: Run the full backend test suite — expect no regressions**

```bash
.venv/bin/python3.11 -m pytest -v 2>&1 | tail -20
```

Expected: all existing tests still pass, plus all new TTS + audio_stream tests. If anything in `tests/policy/` or elsewhere fails, check that the imports at the top of `session.py` weren't accidentally reordered in a way that broke something.

- [ ] **Step 6: Sanity-check that imports resolve**

```bash
.venv/bin/python3.11 -c "from src.main import app; print('app imports clean')"
```

Expected: `app imports clean`.

- [ ] **Step 7: Commit**

```bash
git add src/api/session.py
git commit -m "[Feat] Stream audio_sentence frames alongside chunk frames in session"
```

---

## Task 5: Frontend `AudioQueue` class (TDD)

**Files:**
- Create: `milo-front/src/audio/audioQueue.js`
- Create: `milo-front/src/audio/__tests__/audioQueue.test.js`

> Frontend tasks run from `/Users/saints/Desktop/ITBA/PF/milo-front/`.

- [ ] **Step 1: Write the failing test for "enqueue starts playback"**

Create `src/audio/__tests__/audioQueue.test.js`:

```javascript
const playSpy = jest.fn(() => Promise.resolve());
const pauseSpy = jest.fn();

class FakeAudio {
  constructor(url) {
    this.url = url;
    this.onended = null;
    this.onerror = null;
  }
  play() {
    playSpy(this.url);
    return Promise.resolve();
  }
  pause() {
    pauseSpy();
  }
}

beforeEach(() => {
  playSpy.mockClear();
  pauseSpy.mockClear();
  global.Audio = FakeAudio;
  global.URL.createObjectURL = jest.fn(() => 'blob:fake-url');
  global.URL.revokeObjectURL = jest.fn();
  // atob is available in modern Node test envs; if not, polyfill:
  if (typeof global.atob !== 'function') {
    global.atob = (str) => Buffer.from(str, 'base64').toString('binary');
  }
});

test('enqueue triggers play of the first item', async () => {
  const AudioQueue = require('../audioQueue').default;
  const q = new AudioQueue();
  // base64 of "AB" is "QUI="
  q.enqueue('QUI=', 'audio/mp3');
  // wait a microtask for the promise chain
  await Promise.resolve();
  expect(playSpy).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run — expect FAIL (module not found)**

From `milo-front/`:

```bash
CI=true npm test -- --watchAll=false audioQueue
```

Expected: `Cannot find module '../audioQueue'`.

- [ ] **Step 3: Create `src/audio/audioQueue.js`**

```javascript
// Plain ES class — no React. Manages sequential playback of MP3 sentences
// delivered as base64 strings.
export default class AudioQueue {
  constructor() {
    this.queue = [];
    this.playing = false;
    this.currentAudio = null;
    this.currentUrl = null;
  }

  enqueue(base64Data, mime) {
    try {
      const binary = atob(base64Data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }
      const blob = new Blob([bytes], { type: mime || 'audio/mp3' });
      this.queue.push(blob);
      if (!this.playing) {
        this._playNext();
      }
    } catch (err) {
      // Bad base64 → drop this sentence silently
    }
  }

  flush() {
    this.queue.length = 0;
    if (this.currentAudio) {
      try { this.currentAudio.pause(); } catch (_) {}
      this.currentAudio = null;
    }
    if (this.currentUrl) {
      URL.revokeObjectURL(this.currentUrl);
      this.currentUrl = null;
    }
    this.playing = false;
  }

  _playNext() {
    if (this.queue.length === 0) {
      this.playing = false;
      return;
    }
    this.playing = true;
    const blob = this.queue.shift();
    const url = URL.createObjectURL(blob);
    this.currentUrl = url;
    const audio = new Audio(url);
    this.currentAudio = audio;

    const onDone = () => {
      URL.revokeObjectURL(url);
      if (this.currentUrl === url) this.currentUrl = null;
      if (this.currentAudio === audio) this.currentAudio = null;
      this._playNext();
    };
    audio.onended = onDone;
    audio.onerror = onDone;
    const playResult = audio.play();
    if (playResult && typeof playResult.catch === 'function') {
      playResult.catch(onDone);
    }
  }
}
```

- [ ] **Step 4: Run — expect PASS**

```bash
CI=true npm test -- --watchAll=false audioQueue
```

Expected: 1 passed.

- [ ] **Step 5: Add a failing test for sequential playback**

Append to `src/audio/__tests__/audioQueue.test.js`:

```javascript
test('plays sentences in arrival order one at a time', async () => {
  const AudioQueue = require('../audioQueue').default;
  const audios = [];
  global.Audio = class FakeAudioOrdered {
    constructor(url) {
      this.url = url;
      this.onended = null;
      this.onerror = null;
      audios.push(this);
    }
    play() {
      playSpy(this.url);
      return Promise.resolve();
    }
    pause() {}
  };

  const q = new AudioQueue();
  q.enqueue('QUI=', 'audio/mp3');
  q.enqueue('Q0Q=', 'audio/mp3');
  q.enqueue('RUY=', 'audio/mp3');
  await Promise.resolve();

  // Only the first should have played so far
  expect(playSpy).toHaveBeenCalledTimes(1);

  // Simulate the first finishing
  audios[0].onended();
  await Promise.resolve();
  expect(playSpy).toHaveBeenCalledTimes(2);

  // And the second
  audios[1].onended();
  await Promise.resolve();
  expect(playSpy).toHaveBeenCalledTimes(3);
});
```

- [ ] **Step 6: Run — expect PASS**

```bash
CI=true npm test -- --watchAll=false audioQueue
```

Expected: 2 passed.

- [ ] **Step 7: Add a failing test for `flush`**

Append:

```javascript
test('flush clears the queue and stops current playback', async () => {
  const AudioQueue = require('../audioQueue').default;
  const q = new AudioQueue();
  q.enqueue('QUI=', 'audio/mp3');
  q.enqueue('Q0Q=', 'audio/mp3');
  await Promise.resolve();
  q.flush();
  expect(pauseSpy).toHaveBeenCalled();
  expect(q.queue.length).toBe(0);
  expect(q.playing).toBe(false);
});
```

- [ ] **Step 8: Run — expect PASS**

```bash
CI=true npm test -- --watchAll=false audioQueue
```

Expected: 3 passed.

- [ ] **Step 9: Commit**

```bash
git add src/audio/audioQueue.js src/audio/__tests__/audioQueue.test.js
git commit -m "[Feat] Add AudioQueue class for sequential MP3 playback"
```

---

## Task 6: Wire `audio_sentence` frames into `sessionSocket.js`

**Files:**
- Modify: `milo-front/src/api/sessionSocket.js`

- [ ] **Step 1: Read the current message-dispatch code**

Open `src/api/sessionSocket.js` and find the `onmessage` (or equivalent) handler. Look for an existing dispatch on `msg.type === 'chunk'`. The existing pattern likely calls a callback like `this.onToken(msg.text)`. The new `audio_sentence` frame is dispatched the same way.

- [ ] **Step 2: Add an `onAudioSentence` callback**

In the constructor / connect signature of `SessionSocket`, accept a new optional callback parameter `onAudioSentence`. Store it on the instance.

In the message-dispatch switch/if-chain, add a new branch:

```javascript
if (msg.type === 'audio_sentence') {
  if (typeof this.onAudioSentence === 'function') {
    this.onAudioSentence({
      seq: msg.seq,
      mime: msg.mime,
      voice: msg.voice,
      data: msg.data,
    });
  }
  return;
}
```

The exact placement depends on how the existing switch is structured. The implementer should match the surrounding style (if it uses `switch`, add a `case`; if it uses `if`/`else if`, add the branch).

- [ ] **Step 3: Run all frontend tests — confirm no regressions**

From `milo-front/`:

```bash
CI=true npm test -- --watchAll=false
```

Expected: all previously-passing tests still pass (including the 3 audioQueue tests just added). The new branch on `audio_sentence` has no test in this task — its behavior is covered end-to-end by the manual smoke test in Task 8.

- [ ] **Step 4: Commit**

```bash
git add src/api/sessionSocket.js
git commit -m "[Feat] Dispatch audio_sentence frames to onAudioSentence callback"
```

---

## Task 7: Mount `AudioQueue` in the conversation view

**Files:**
- Modify: the file that instantiates `SessionSocket` and consumes its callbacks (likely `milo-front/src/pages/` or `milo-front/src/components/MessageThread.js`)

- [ ] **Step 1: Locate the `SessionSocket` consumer**

From `milo-front/`:

```bash
grep -rn "new SessionSocket\|SessionSocket(" src/ --include="*.js" | head
```

Find the file where the socket is constructed. Inside that file, `onToken` (or equivalent) is wired up.

- [ ] **Step 2: Import `AudioQueue` and instantiate it**

At the top of that file, add:

```javascript
import AudioQueue from '../audio/audioQueue';
```

(Adjust the relative path to match where the consumer file lives.)

Inside the React component, hold the queue in a ref so it survives re-renders:

```javascript
const audioQueueRef = useRef(null);
if (!audioQueueRef.current) {
  audioQueueRef.current = new AudioQueue();
}
```

- [ ] **Step 3: Wire the `onAudioSentence` callback**

When constructing the `SessionSocket` (or passing its config), add:

```javascript
onAudioSentence: ({ seq, mime, voice, data }) => {
  audioQueueRef.current.enqueue(data, mime);
},
```

(The `seq` and `voice` aren't used in PR 2 — the queue plays in arrival order; backend emits in order because synth is sequential per Task 3.)

- [ ] **Step 4: Flush the queue on unmount or session end**

In the same component, add an effect that calls `audioQueueRef.current.flush()` on unmount or when the session changes:

```javascript
useEffect(() => {
  const queue = audioQueueRef.current;
  return () => {
    queue.flush();
  };
}, []);
```

If there's an existing session-cleanup effect, attach the flush call there instead.

- [ ] **Step 5: Run all frontend tests — confirm no regressions**

```bash
CI=true npm test -- --watchAll=false
```

Expected: all tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/pages src/components 2>/dev/null  # adjust paths to whatever actually changed
git commit -m "[Feat] Wire AudioQueue to play audio_sentence frames in chat view"
```

---

## Task 8: Manual smoke test (full one-way voice output)

**Files:** none — verification only.

- [ ] **Step 1: Restart the backend**

From `milo-back-agent-orchestrator/`:

```bash
.venv/bin/python3.11 -m uvicorn src.main:app --reload --port 8000
```

Expected: server starts, logs show "Application startup complete" and listens on `:8000`.

- [ ] **Step 2: Restart the frontend**

From `milo-front/`:

```bash
npm start
```

Browser opens at `localhost:3000`.

- [ ] **Step 3: Sign in and open an activity**

Use the existing login flow. Navigate to any chat activity.

- [ ] **Step 4: Send a typed message in English**

Type *"What is the capital of France?"* and click Send. Expected: Milo's response streams as text AS USUAL, AND the audio plays sentence-by-sentence within ~2–3 seconds of the first text token. Voice should be an English voice (`en-US-AriaNeural` by default).

- [ ] **Step 5: Send a typed message in Spanish**

Type *"¿Cuál es la capital de Francia?"* — Milo should respond in Spanish (because the LLM context says so), and the audio should use the Spanish voice (`es-MX-DaliaNeural`). The Spanish detection heuristic should fire on the first sentence.

- [ ] **Step 6: Test the voice lock**

Send a question that's likely to make Milo code-switch (e.g., *"Hola, can you explain this concept in English?"*). Expected: the voice picked for the first sentence stays for the whole response — no mid-response voice flip.

- [ ] **Step 7: Test interceptor correction (if you have a test prompt that triggers it)**

If you know a prompt that the policy interceptor fires on, send it and confirm that:
- The base response is streamed and synthesized normally.
- After the LLM stream ends, an appended correction chunk arrives as text AND as one trailing audio sentence in the same voice.

If you don't know how to trigger it manually, skip — it's covered by the unit tests in Task 3.

- [ ] **Step 8: Test mic + voice loop together (PR 1 + PR 2)**

Click 🎤, speak a Spanish question, click again to stop. Transcript appears → click Send → response streams as text → audio plays. Full voice loop minus barge-in.

- [ ] **Step 9: Confirm no console errors**

Open browser devtools console. Expected: no red errors during steps 4–8. Acceptable warnings: React StrictMode double-mount, autoplay-policy nudges if any.

- [ ] **Step 10: Confirm backend logs are clean**

In the uvicorn terminal, look for any new TTS/audio_stream tracebacks. Acceptable: `TTS failed for sentence (silent degrade)` warnings if EdgeTTS has a flaky moment. Not acceptable: 500s on `/chat/activities/...` WebSocket or any unhandled exceptions in the session loop.

- [ ] **Step 11: Done — no commit needed**

If steps 4–8 all worked, PR 2 is shippable. If not, fix and re-run.

---

## Done criteria

PR 2 is shippable when:
- All backend tests pass (`pytest -v` shows the full suite green, including 3 new TTS tests and 5 new audio_stream tests).
- All frontend tests pass (`CI=true npm test -- --watchAll=false` shows the full suite green, including 3 new audioQueue tests).
- Manual smoke test (Task 8) passes in both English and Spanish.
- No regressions in the existing chat flow (typed text → agent response still streams as text correctly even if audio is disabled in the browser).

---

## Follow-ups deferred from PR 2

These are intentionally out of scope and tracked here so PR 3 / a follow-up can pick them up:

- **Barge-in** — `cancel_turn` WS frame, asyncio.CancelScope around the session stream loop, frontend mic-cancels-audio. Entire PR 3 scope.
- **`<StartVoiceSessionButton>` autoplay unlock** — explicit user-gesture button to satisfy browser autoplay policy when joining a session that's already mid-response. Currently the existing "send" or "mic" gesture covers it.
- **Concurrent sentence TTS** — synth N sentences in parallel and emit in seq order. Reduces tail-of-response latency. Not needed for v2; sequential is acceptable.
- **Language hint propagation from frontend** — frontend collects the last assistant message's language and forwards it to `/audio/transcribe` (Q9 from spec). Add this in a small follow-up; backend already accepts the param.
- **`<audio>`-based playback → Web Audio API** — switching from `new Audio()` to a true `AudioContext` lets us gap-free queue, mix, and apply effects. Not needed unless seams between sentences become a real UX complaint.
