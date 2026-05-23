from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ApprovalInfo
from app.schemas.schedule import (
    ConflictItem,
    ConflictSuggestion,
    ReminderJobInfo,
    ScheduleEventDraft,
)


class McpResultBase(BaseModel):
    status: str = "ok"
    message: str | None = None
    error_code: str | None = None


class McpParseScheduleDraftResult(McpResultBase):
    draft: ScheduleEventDraft | None = None
    draft_hash: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    ambiguity_flags: list[str] = Field(default_factory=list)
    evidence_digest: list[str] = Field(default_factory=list)
    parse_confidence: float | None = None


class McpDetectScheduleConflictsResult(McpResultBase):
    conflict_items: list[ConflictItem] = Field(default_factory=list)
    suggestions: list[ConflictSuggestion] = Field(default_factory=list)
    risk_level: str | None = None
    approval: ApprovalInfo | None = None


class McpCreateScheduleAfterApprovalResult(McpResultBase):
    schedule_id: int | None = None
    reminder_jobs: list[ReminderJobInfo] = Field(default_factory=list)


class McpRecordQuickNoteResult(McpResultBase):
    normalized_content: str | None = None
    preview_tags: list[str] = Field(default_factory=list)
    attachment_ids: list[int] = Field(default_factory=list)
    evidence_digest: list[str] = Field(default_factory=list)
    approval: ApprovalInfo | None = None
    note_id: int | None = None
    topic_tags: list[str] = Field(default_factory=list)


class McpDispatchNotificationResult(McpResultBase):
    delivery_id: int | None = None
    delivery_status: str | None = None
    provider: str | None = None


class McpGetNotificationStatusResult(McpResultBase):
    delivery_id: int | None = None
    channel_status: str | None = None
    retry_info: dict[str, Any] | None = None
