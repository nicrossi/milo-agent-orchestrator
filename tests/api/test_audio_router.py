from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_transcribe_returns_text_on_success(client):
    with patch("src.api.routers.audio.stt.transcribe", new=AsyncMock(return_value="hola")):
        files = {"file": ("audio.webm", b"\x00\x01\x02", "audio/webm")}
        response = await client.post("/audio/transcribe", files=files)

    assert response.status_code == 200
    assert response.json() == {"text": "hola"}


@pytest.mark.asyncio
async def test_transcribe_rejects_oversize_upload(client):
    big_blob = b"x" * (26 * 1024 * 1024)
    files = {"file": ("audio.webm", big_blob, "audio/webm")}
    response = await client.post("/audio/transcribe", files=files)
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_transcribe_rejects_empty_upload(client):
    files = {"file": ("audio.webm", b"", "audio/webm")}
    response = await client.post("/audio/transcribe", files=files)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_transcribe_returns_502_when_stt_errors(client):
    from src.services.stt import STTError

    with patch(
        "src.api.routers.audio.stt.transcribe",
        new=AsyncMock(side_effect=STTError("whisper down")),
    ):
        files = {"file": ("audio.webm", b"\x00\x01", "audio/webm")}
        response = await client.post("/audio/transcribe", files=files)

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_transcribe_forwards_language_hint(client):
    captured = {}

    async def fake_transcribe(audio_bytes, mime, language_hint=None):
        captured["language_hint"] = language_hint
        return "hola"

    with patch("src.api.routers.audio.stt.transcribe", new=fake_transcribe):
        files = {"file": ("audio.webm", b"\x00", "audio/webm")}
        data = {"language_hint": "es"}
        response = await client.post("/audio/transcribe", files=files, data=data)

    assert response.status_code == 200
    assert captured["language_hint"] == "es"


@pytest.mark.asyncio
async def test_transcribe_requires_auth_when_enabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    files = {"file": ("audio.webm", b"\x00", "audio/webm")}
    response = await client.post("/audio/transcribe", files=files)
    assert response.status_code == 401
