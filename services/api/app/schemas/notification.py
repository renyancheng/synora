from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NotificationItem(BaseModel):
    id: int
    channel: str
    provider: str
    recipient: str
    subject: str
    body: str | None = None
    status: str
    error_message: str | None = None
    retry_count: int
    created_at: datetime
    delivered_at: datetime | None = None

