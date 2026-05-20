import asyncio
import logging

import edge_tts

logger = logging.getLogger("milo-orchestrator.tts")


class TTSError(Exception):
    """Raised when text-to-speech synthesis fails terminally."""


_SYNTHESIS_TIMEOUT_S = 10.5  # per-attempt wall-clock budget for EdgeTTS


async def synthesize(text: str, voice: str) -> bytes:
    """Synthesize `text` with EdgeTTS voice `voice`. Returns MP3 bytes.

    On a single transient failure, retries once with a ~500 ms timeout.
    Raises TTSError on terminal failure.
    """
    try:
        return await asyncio.wait_for(_synthesize_once(text, voice), timeout=_SYNTHESIS_TIMEOUT_S)
    except Exception as first_exc:
        logger.warning("EdgeTTS first attempt failed: %s — retrying", first_exc)
        try:
            return await asyncio.wait_for(_synthesize_once(text, voice), timeout=_SYNTHESIS_TIMEOUT_S)
        except Exception as second_exc:
            logger.warning("EdgeTTS retry also failed: %s", second_exc)
            raise TTSError(str(second_exc)) from second_exc


async def _synthesize_once(text: str, voice: str) -> bytes:
    communicate = edge_tts.Communicate(text, voice)
    chunks: list[bytes] = []
    async for event in communicate.stream():
        if event.get("type") == "audio":
            data = event.get("data")
            if data:
                chunks.append(data)
    return b"".join(chunks)
