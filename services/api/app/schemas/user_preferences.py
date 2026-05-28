from __future__ import annotations

from pydantic import BaseModel


class UserPreferencesResponse(BaseModel):
    status: str = "ok"
    wecom_robot_webhook: str | None = None


class UserPreferencesUpdateRequest(BaseModel):
    wecom_robot_webhook: str | None = None
