from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ApprovalInfo


class QuickNoteRequest(BaseModel):
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    approval_token: str | None = None


class QuickNotePreviewResponse(BaseModel):
    status: str = "pending_approval"
    preview_tags: list[str]
    approval: ApprovalInfo


class QuickNoteSavedResponse(BaseModel):
    status: str = "ok"
    note_id: int
    topic_tags: list[str]


class QuickNoteItem(BaseModel):
    id: int
    content: str
    tags: list[str]
    created_at: datetime
