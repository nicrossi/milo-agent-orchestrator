from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _force_whisper_provider(monkeypatch):
    # These tests target the Whisper code path; pin the env so a developer's
    # local .env (which may set STT_PROVIDER=gemini) doesn't reroute us.
    monkeypatch.setenv("STT_PROVIDER", "whisper")


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


@pytest.mark.asyncio
async def test_transcribe_raises_stt_error_on_api_failure():
    from src.services.stt import STTError, transcribe

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("src.services.stt._get_client", return_value=mock_client):
        with pytest.raises(STTError):
            await transcribe(audio_bytes=b"x", mime="audio/webm")
