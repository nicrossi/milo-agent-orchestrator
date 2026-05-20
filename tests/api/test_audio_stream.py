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
    # When the middle sentence's TTS fails, seq still advances — frontend sees gap.
    assert [e.seq for e in audio_events] == [0, 2]


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
