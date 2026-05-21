import logging
import os
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger("milo-orchestrator.stt")


class STTError(Exception):
    """Raised when speech-to-text transcription fails terminally."""


# Provider selection — set STT_PROVIDER=gemini to route to Gemini Flash audio
# input (same credit pool as the LLM). Defaults to OpenAI Whisper. Read
# dynamically so tests and runtime config changes are honored without
# re-importing the module.
def _current_provider() -> str:
    return os.getenv("STT_PROVIDER", "whisper").strip().lower()


def _current_gemini_model() -> str:
    return os.getenv("STT_GEMINI_MODEL", "gemini-2.5-flash")
# System-prompt-style directive to keep Gemini from adding commentary.
_GEMINI_TRANSCRIBE_INSTRUCTION = (
    "Transcribe the audio verbatim into text. Preserve the speaker's original "
    "language. Return ONLY the transcript text — no explanations, no quotation "
    "marks, no labels. If the audio is silent or unintelligible, return an empty string."
)

_whisper_client: Optional[AsyncOpenAI] = None
_gemini_client = None  # google.genai.Client — typed lazily


def _get_client() -> AsyncOpenAI:
    """Lazy OpenAI client used by the Whisper transcription path."""
    global _whisper_client
    if _whisper_client is None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise STTError("OPENAI_API_KEY is not configured.")
        _whisper_client = AsyncOpenAI(api_key=api_key)
    return _whisper_client


def _get_gemini_client():
    """Lazy genai client. Mirrors the auth probing in GeminiAdapter so STT can
    share Vertex or API-key credentials without duplicating env wiring.
    """
    global _gemini_client
    if _gemini_client is None:
        import google.genai as genai  # local import keeps Whisper-only deploys lean
        vertex_project = os.getenv("VERTEX_PROJECT")
        vertex_location = os.getenv("VERTEX_LOCATION", "us-central1")
        if vertex_project:
            _gemini_client = genai.Client(
                vertexai=True, project=vertex_project, location=vertex_location
            )
        else:
            api_key = os.getenv("GOOGLE_API_KEY", "").strip()
            if not api_key:
                raise STTError("GOOGLE_API_KEY or VERTEX_PROJECT is required for Gemini STT.")
            _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


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


def _normalize_mime_for_gemini(mime: str) -> str:
    """Gemini accepts a small set of audio MIME types. Map our common values
    onto the canonical ones it advertises so uploads aren't rejected for naming.
    """
    m = (mime or "").lower()
    if "wav" in m:
        return "audio/wav"
    if "webm" in m:
        return "audio/webm"
    if "ogg" in m:
        return "audio/ogg"
    if "mp3" in m or "mpeg" in m:
        return "audio/mp3"
    if "mp4" in m or "m4a" in m or "aac" in m:
        return "audio/aac"
    if "flac" in m:
        return "audio/flac"
    return "audio/wav"


async def _transcribe_whisper(
    audio_bytes: bytes, mime: str, language_hint: Optional[str]
) -> str:
    client = _get_client()
    filename = _filename_for_mime(mime)
    kwargs = {
        "model": "whisper-1",
        "file": (filename, audio_bytes, mime),
    }
    if language_hint:
        kwargs["language"] = language_hint
    try:
        response = await client.audio.transcriptions.create(**kwargs)
    except Exception as exc:
        logger.warning("Whisper transcription failed: %s", exc)
        raise STTError(str(exc)) from exc
    return response.text


async def _transcribe_gemini(
    audio_bytes: bytes, mime: str, language_hint: Optional[str]
) -> str:
    from google.genai import types  # local import — see _get_gemini_client
    client = _get_gemini_client()
    instruction = _GEMINI_TRANSCRIBE_INSTRUCTION
    if language_hint:
        instruction = (
            f"{instruction} The expected language is {language_hint}."
        )
    try:
        response = await client.aio.models.generate_content(
            model=_current_gemini_model(),
            contents=[
                types.Part.from_bytes(
                    data=audio_bytes,
                    mime_type=_normalize_mime_for_gemini(mime),
                ),
                instruction,
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=2048,
            ),
        )
    except Exception as exc:
        logger.warning("Gemini transcription failed: %s", exc)
        raise STTError(str(exc)) from exc
    return (response.text or "").strip()


async def transcribe(
    audio_bytes: bytes,
    mime: str,
    language_hint: Optional[str] = None,
) -> str:
    if _current_provider() == "gemini":
        return await _transcribe_gemini(audio_bytes, mime, language_hint)
    return await _transcribe_whisper(audio_bytes, mime, language_hint)
