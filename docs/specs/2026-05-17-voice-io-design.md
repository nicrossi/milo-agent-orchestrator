# Voice I/O — Design Spec

**Date:** 2026-05-17
**Status:** Draft (pending user review)
**Scope:** `milo-back-agent-orchestrator` + `milo-front`

## Summary

Adds a full voice loop to Milo: students can speak their questions (push-to-talk capture → OpenAI Whisper transcription) and hear the agent's response (sentence-streamed EdgeTTS playback). Text chat stays the default; voice is an opt-in alternative gated by an explicit "Start voice session" button.

## Goals

- Accessibility / hands-free interaction with Milo.
- Low perceived latency: student hears the first sentence within ~2–3 s of LLM start.
- Zero new vendors for TTS (EdgeTTS is free); minimal vendor surface for STT (single OpenAI Whisper dependency).
- No audio retention.

## Non-goals

- Persistent audio (caching, replay, audit).
- Always-listening / wake-word interaction.
- Languages beyond Spanish + English.
- True token-streamed TTS (sentence-streamed is the target).
- Voice-only sessions without a screen — text rendering remains the source of truth.

## Locked decisions

| # | Topic | Decision |
|---|-------|----------|
| 1 | Use case | Full voice loop, additive to text chat |
| 2 | STT capture | Push-to-talk (click-start / click-stop) |
| 3 | TTS delivery | Sentence-streamed, sequential playback |
| 4 | STT vendor | OpenAI Whisper API (`whisper-1`) |
| 5 | Transport | Base64 MP3 inside JSON WebSocket frames |
| 6 | Persistence | Ephemeral — no audio storage |
| 7 | Interceptor corrections in voice | Synthesize the appended correction as one final audio sentence |
| 8 | Voice picker | Two fixed voices (one Spanish, one English); locked for the whole voice session (first response's detected language) |
| 9 | Whisper language hint | Hint from prior assistant message; autodetect on first turn |
| 10 | Barge-in | Cancel + interrupt mid-stream via new WS `cancel_turn` frame |
| 11 | TTS failure | One retry (~500 ms), then silent degrade for that sentence |
| 12 | First-turn greeting | Opt-in "Start voice session" button gates the whole session |

## Architecture

```
[Frontend / milo-front]                          [Backend / milo-back-agent-orchestrator]

  StartVoiceSessionButton  (one user gesture unlocks
        │                   browser autoplay for the session)
        ▼
  MicButton (PTT)          multipart audio              POST /audio/transcribe
  useAudioCapture     ───────────────────────────────►  routers/audio.py
  (MediaRecorder API)                                          │
        │                                                      ▼
        │                                                services/stt.py ─► OpenAI Whisper
        │                  { text: "..." }                     │
        │  ◄────────────────────────────────────────────  returns transcript
        ▼
  sessionSocket.sendMessage(text)
        │
        │   existing WebSocket  /chat/activities/{id}
        ▼
                                                         api/session.py
                                                              │
                                                              ▼
                                                         api/audio_stream.py
                                                         (wraps LLM stream,
                                                          splits sentences,
                                                          locks voice per session)
                                                              │
                                                              ├─► services/tts.py ─► EdgeTTS
                                                              │
                                                              └─► reuses
                                                                 split_sentences()
                                                                 from policy/interceptors/
                                                                 open_endedness_classifier.py
  AudioQueuePlayer  ◄── audio_sentence frames (base64 MP3) ────┘
  TextRenderer      ◄── chunk frames (existing) ───────────────┘
```

**Shape recap:** STT is a one-shot HTTP request. TTS is interleaved into the existing WebSocket stream alongside text chunks. One new wrapper module (`api/audio_stream.py`) sits between the LLM stream and the WS frame emitter — `session.py` iterates over it instead of the raw LLM stream.

## Components

### Backend

**`src/services/tts.py`** — EdgeTTS async wrapper.

- `async synthesize(text: str, voice: str) -> bytes` — returns full MP3 bytes for one sentence.
- Internal retry (1×, ~500 ms timeout) per Q11.
- Raises `TTSError` on terminal failure; caller decides whether to skip or hard-fail.

**`src/services/stt.py`** — Whisper API async wrapper.

- `async transcribe(audio_bytes: bytes, mime: str, language_hint: Optional[str]) -> str` per Q9.
- Raises `STTError` on terminal failure.

**`src/api/audio_stream.py`** — wraps an `AsyncIterator[str]` (the LLM stream) and yields `AudioStreamEvent` items: `TextChunk(text)` or `AudioSentence(seq, mp3_bytes, voice)`.

- Sentence-splitting **reuses** `split_sentences()` from [open_endedness_classifier.py:88](../../src/policy/interceptors/open_endedness_classifier.py#L88). Does not fork; if decimals/abbreviations matter for TTS, the shared splitter gets improved (and the interceptor's contract benefits too).
- Voice lock: the first turn's first sentence picks the voice via language detection; `ChatSession` persists it (`self._voice`) and passes it into `audio_stream.wrap(voice=...)` on every subsequent turn, so all responses in the session keep one voice/accent even when the reply language changes across turns.
- Concurrent: when a sentence boundary is detected, TTS fires while LLM keeps streaming.
- Cancellation: respects an `asyncio.CancelScope` so the WS handler can drop everything on barge-in (Q10).

**`src/api/routers/audio.py`** — new `POST /audio/transcribe` endpoint.

- Auth: reuses `require_http_user` from [core/auth.py](../../src/core/auth.py).
- Limits: 25 MB max upload (Whisper hard cap); per-user rate limit (see Open implementation questions).
- Accepts `multipart/form-data` with `file` and optional `language_hint`.
- Returns `{"text": "..."}`.

**Edits to `src/api/session.py`:**

- Around [line 390](../../src/api/session.py#L390): replace iteration over raw LLM stream with iteration over `audio_stream.wrap(...)`. Emit `audio_sentence` WS frames alongside existing `chunk` frames.
- When `_policy_engine.check_output()` appends a correction (Q7), pipe it through `tts.synthesize()` and emit one final `audio_sentence` frame before `done`.
- New WS frame handler: `cancel_turn`. The session holds an `asyncio.Task` reference for the in-flight turn; on cancel, calls `.cancel()` and emits `{"type": "cancelled"}` to the client.

### Frontend

**`useAudioCapture` hook** — wraps `MediaRecorder` API. Exposes `{ startRecording, stopRecording, isRecording, lastBlob }`. WebM/Opus output (broad browser support; Whisper accepts it).

**`<MicButton>`** — push-to-talk control. Click to start, click to stop. Disabled during transcription. On stop: uploads blob to `/audio/transcribe`, then on response sends transcript via `sessionSocket.sendMessage(text)`. If audio is currently playing, sends `cancel_turn` and flushes the audio queue before starting recording (Q10).

**`<AudioQueuePlayer>`** — receives `audio_sentence` frames over the existing WebSocket, queues MP3 blobs, plays sequentially via Web Audio API (avoids gaps between `<audio>` elements). Exposes `flushQueue()` for barge-in.

**`<StartVoiceSessionButton>`** — one-time gesture per session that unlocks browser audio autoplay (Q12). Once clicked, voice mode is "on" for the session; greeting audio plays automatically; subsequent turns auto-play.

## Data flow — full voice turn

1. Student clicks **MicButton** (voice mode already active).
2. If audio is currently playing: send `cancel_turn` WS frame, flush the queue.
3. Browser records WebM/Opus until student clicks again.
4. Blob uploaded to `POST /audio/transcribe` with `language_hint` derived from the last assistant message.
5. Whisper transcribes. Backend returns `{text}`.
6. Frontend sends `text` via the existing WS path.
7. Backend agent runs; LLM stream begins.
8. `audio_stream.wrap()` splits the stream into sentences as they arrive.
9. Per sentence: voice chosen on the session's first turn (language detected on its first sentence) and reused for every later turn → TTS synth → base64 → `audio_sentence` WS frame.
10. Frontend renders text chunks AND queues MP3 sentences for sequential playback.
11. LLM stream ends. Policy interceptor may append a correction → synthesized as one final audio sentence → emitted.
12. `done` frame sent.

## Error handling

| Failure | Behavior |
|---------|----------|
| Whisper API error | HTTP 502 from `/audio/transcribe`; frontend shows toast "Couldn't transcribe — try again." |
| Audio upload > 25 MB | HTTP 413; frontend shows "Recording too long." |
| EdgeTTS failure on a sentence | 1 retry (500 ms). On terminal failure: skip that sentence's audio, log, keep text flowing. Other sentences in the same turn still attempted. |
| `cancel_turn` mid-stream | Backend asyncio-cancels the agent turn; flushes pending sentence work; emits `{type: "cancelled"}`. |
| Browser blocks autoplay | The `<StartVoiceSessionButton>` gesture (Q12) is the unlock. If user lands without it: graceful degrade to text-only with a one-line nudge. |
| Microphone permission denied | `<MicButton>` shows a permission-prompt explainer; voice mode disables until granted. |

## Testing

**Backend unit:**

- `audio_stream.wrap()` — given a fixed token sequence, asserts correct sentence boundaries via the real `split_sentences()`.
- `audio_stream.wrap()` — voice lock holds across mid-response code-switching; a pre-locked `voice` argument skips detection entirely (session lock).
- Cancellation — `asyncio.CancelScope` drops in-flight TTS calls cleanly.
- `tts.synthesize()` — retry-once behavior via mocked `edge-tts`.
- `stt.transcribe()` — `language_hint` propagation via mocked OpenAI client.

**Backend integration:**

- `POST /audio/transcribe` — auth required (401 without token), 413 on oversize, happy-path with mocked Whisper.
- WS session — synthetic LLM stream, assert correct frame interleaving (`chunk`, `audio_sentence`, final correction `audio_sentence`, `done`).
- WS `cancel_turn` — mid-stream cancellation cleanly terminates without orphan tasks.

**Frontend (manual smoke):**

- Mic permission flow.
- Push-to-talk → transcript → response audio play-through (English).
- Same in Spanish.
- Barge-in: start speaking mid-audio, assert audio stops and new turn begins.
- TTS failure simulation (mock backend dropping a sentence): assert text still renders, audio gap is silent (no error toast).
- Browser autoplay path: load chat in voice mode without clicking start → graceful text-only fallback.

## Rollout

Three PRs in order; each ships and is testable independently.

**PR 1 — STT only.** `/audio/transcribe` endpoint + frontend mic capture.

- New: `services/stt.py`, `routers/audio.py`, `useAudioCapture` hook, `<MicButton>`.
- No TTS yet — student speaks, sees transcript appear in input box, sends as text.
- Demoable; ships hands-free input value on its own.

**PR 2 — TTS sentence-streaming.** No barge-in yet.

- New: `services/tts.py`, `api/audio_stream.py`, edits to `session.py` around line 390, new `audio_sentence` WS frame, `<AudioQueuePlayer>`.
- Student types, hears the response. Voice mode still works without barge-in (mic disabled while audio plays).
- Demoable; full one-way voice output works.

**PR 3 — Voice session UX glue.** Barge-in + opt-in greeting.

- New: `<StartVoiceSessionButton>`, WS `cancel_turn` frame + handler in `session.py`, `<AudioQueuePlayer>.flushQueue()`, `<MicButton>` barge-in wiring.
- Full Q10 loop complete; greeting respects autoplay constraint (Q12).

## Open implementation questions

These don't change the design; they're decisions to make during implementation and are flagged so they don't get lost.

- **Specific voice IDs.** Pick concrete EdgeTTS voice names (e.g., `es-MX-DaliaNeural` vs `es-ES-ElviraNeural`; `en-US-AriaNeural` vs `en-US-JennyNeural`). Decide during PR 2.
- **Rate-limit middleware shape.** Per-user token bucket vs. global IP limit vs. none for v1. Follow whatever pattern exists in the FastAPI app; if nothing exists, simplest in-memory token bucket per user. Decide during PR 1.
- **Language detection for voice lock.** Cheapest reliable signal on the first ~50 characters of LLM output. Candidates: a regex heuristic (inverted `¿¡` or common Spanish stopwords) or a lightweight classifier like `langid`/`langdetect`. Decide during PR 2.
