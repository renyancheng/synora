from __future__ import annotations

from pydantic import BaseModel


class UserPreferencesResponse(BaseModel):
    status: str = "ok"


class UserPreferencesUpdateRequest(BaseModel):
    pass
