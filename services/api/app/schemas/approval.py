from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ApprovalItem(BaseModel):
    id: int
    action: str
    draft_hash: str
    status: str
    expires_at: datetime
    created_at: datetime
    confirmed_at: datetime | None = None
