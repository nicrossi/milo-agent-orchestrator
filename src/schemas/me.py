from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    uid: str
    email: str
    display_name: str
    role: str


class MeUpdateRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=255)
