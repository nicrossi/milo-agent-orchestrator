from pydantic import BaseModel, Field


class TranscribeResponse(BaseModel):
    text: str = Field(..., description="Transcribed text from the uploaded audio.")
