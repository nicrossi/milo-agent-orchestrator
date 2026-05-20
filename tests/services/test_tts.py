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
