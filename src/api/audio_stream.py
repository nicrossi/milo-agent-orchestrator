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
