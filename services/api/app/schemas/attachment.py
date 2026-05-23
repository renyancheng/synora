from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AttachmentUploadResponse(BaseModel):
    status: str = "ok"
    attachment_id: int
    file_name: str
    content_type: str
    size_bytes: int
    created_at: datetime
