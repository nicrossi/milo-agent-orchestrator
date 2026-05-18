import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.core.auth import AuthenticatedUser, require_http_user
from src.schemas.audio import TranscribeResponse
from src.services import stt

logger = logging.getLogger("milo-orchestrator.audio")

router = APIRouter(prefix="/audio", tags=["Audio"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # Whisper API hard limit


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    language_hint: Optional[str] = Form(default=None),
    user: AuthenticatedUser = Depends(require_http_user),
) -> TranscribeResponse:
    audio_bytes = await file.read()
    if len(audio_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Audio exceeds {MAX_UPLOAD_BYTES} bytes.",
        )
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio upload.")

    mime = file.content_type or "audio/webm"

    try:
        text = await stt.transcribe(
            audio_bytes=audio_bytes,
            mime=mime,
            language_hint=language_hint,
        )
    except stt.STTError as exc:
        logger.warning("Transcribe failed for user=%s: %s", user.uid, exc)
        raise HTTPException(status_code=502, detail="Transcription service failed.")

    return TranscribeResponse(text=text)
