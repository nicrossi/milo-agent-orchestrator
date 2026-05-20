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

    try:
        response = await client.audio.transcriptions.create(**kwargs)
    except Exception as exc:
        logger.warning("Whisper transcription failed: %s", exc)
        raise STTError(str(exc)) from exc
    return response.text
