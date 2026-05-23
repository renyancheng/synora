from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ApprovalInfo


class QuickNoteDraftRequest(BaseModel):
    content: str | None = None
    tags: list[str] = Field(default_factory=list)
    attachment_ids: list[int] = Field(default_factory=list)
    context: dict[str, str] = Field(default_factory=dict)


class QuickNoteConfirmRequest(BaseModel):
    approval_token: str
    content: str
    tags: list[str] = Field(default_factory=list)
    attachment_ids: list[int] = Field(default_factory=list)


class QuickNoteDraftResponse(BaseModel):
    status: str = "pending_approval"
    normalized_content: str
    preview_tags: list[str]
    attachment_ids: list[int]
    evidence_digest: list[str]
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
    source_attachment_ids: list[int] = Field(default_factory=list)
