from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import UserSummary


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str


class LoginResponse(BaseModel):
    status: str = "ok"
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserSummary


class CurrentSessionResponse(BaseModel):
    status: str = "ok"
    expires_at: datetime
    user: UserSummary
