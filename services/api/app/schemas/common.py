from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SourceType = Literal["text", "screenshot", "photo", "chat_record", "email"]


class ApiEnvelope(BaseModel):
    status: str = "ok"
    message: str | None = None


class UserSummary(BaseModel):
    id: int
    email: str
    display_name: str


class ApprovalInfo(BaseModel):
    approval_token: str
    action: str
    expires_at: datetime
    draft_hash: str

