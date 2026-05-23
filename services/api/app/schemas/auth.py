from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import UserSummary


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    status: str = "ok"
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserSummary
