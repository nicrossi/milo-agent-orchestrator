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


_VOICE_ES_DEFAULT = "es-US-PalomaNeural"
_VOICE_EN_DEFAULT = "en-US-AriaNeural"


def voice_for_language(language: str) -> str:
    if language == "es":
        return os.getenv("EDGE_TTS_VOICE_ES", _VOICE_ES_DEFAULT)
    return os.getenv("EDGE_TTS_VOICE_EN", _VOICE_EN_DEFAULT)


# Spanish-only characters: ñ + accented vowels. Any occurrence is a strong
# signal even if no stopword matches (English borrows these only in proper
# nouns, which the LLM rarely emits in mid-sentence).
_SPANISH_CHARS = re.compile(r"[ñáéíóúüÑÁÉÍÓÚÜ]")

_SPANISH_MARKERS = re.compile(
    r"[¿¡]|\b("
    r"qué|está|estás|estoy|estamos|están|"
    r"por|para|con|los|las|una|uno|este|esto|esa|ese|eso|"
    r"tú|sí|también|porque|pero|"
    r"cómo|cuándo|dónde|donde|cuando|como|"
    r"hola|gracias|chau|adiós|"
    r"hacer|hacia|puedes|puedo|sobre|"
    r"vamos|voy|soy|son|ser|"
    r"ahora|aquí|allí|muy|más|sin|hasta|"
    r"todo|todos|todas|toda|nada|algo|"
    r"vos|usted|nosotros|ustedes|ellos|ellas|"
    r"bueno|claro|bien"
    r")\b",
    re.IGNORECASE,
)


def detect_language(text: str) -> str:
    """Cheap heuristic: Spanish if Spanish-only chars (ñ, accented vowels),
    inverted punctuation, or common Spanish stopwords appear in the first
    ~120 chars. Otherwise English.
    """
    sample = text[:120]
    if _SPANISH_CHARS.search(sample) or _SPANISH_MARKERS.search(sample):
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
    voice: Optional[str] = None,
) -> AsyncIterator[AudioStreamEvent]:
    """Wrap an LLM text stream, yielding TextChunk and AudioSentence events.

    If `voice` is provided, language detection is skipped and every sentence
    is synthesized with it (session-level voice lock). Otherwise the first
    sentence's detected language picks the voice for this call.
    """
    buffer = ""
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
            # seq still advances on TTS failure so the frontend can detect
            # silently-dropped sentences as gaps in the sequence.
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
