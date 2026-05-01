from typing import Optional

from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    uid: str
    email: str
    display_name: str
    role: str
    photo_data_url: Optional[str] = None


class MeUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    # Use empty string or null to clear the avatar; absent field = no change.
    # Cap at ~1.5MB (data URL overhead included) to keep DB rows reasonable.
    photo_data_url: Optional[str] = Field(None, max_length=2_000_000)
