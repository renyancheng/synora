from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ApprovalInfo


class ScheduleDraftInput(BaseModel):
    input_text: str = Field(min_length=1)
    context: dict[str, str] = Field(default_factory=dict)


class ScheduleDraft(BaseModel):
    title: str
    location: str | None = None
    details: str
    source_text: str
    scheduled_at: datetime | None = None
    duration_minutes: int = 60
    reminder_at: datetime | None = None


class ScheduleDraftResponse(BaseModel):
    status: str = "ok"
    draft: ScheduleDraft
    draft_hash: str
    missing_fields: list[str]
    ambiguity_flags: list[str]


class ConflictCheckRequest(BaseModel):
    draft: ScheduleDraft
    draft_hash: str


class ConflictItem(BaseModel):
    schedule_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None = None


class ConflictSuggestion(BaseModel):
    label: str
    candidate_start: datetime
    candidate_end: datetime


class ConflictCheckResponse(BaseModel):
    status: str = "ok"
    conflict_items: list[ConflictItem]
    suggestions: list[ConflictSuggestion]
    risk_level: str
    approval: ApprovalInfo


class ScheduleConfirmRequest(BaseModel):
    approval_token: str
    normalized_draft: ScheduleDraft


class ReminderJobInfo(BaseModel):
    id: int
    channel: str
    scheduled_for: datetime
    status: str


class ScheduleConfirmResponse(BaseModel):
    status: str = "ok"
    schedule_id: int
    reminder_jobs: list[ReminderJobInfo]


class ScheduleItem(BaseModel):
    id: int
    title: str
    location: str | None = None
    details: str
    scheduled_at: datetime
    duration_minutes: int
    reminder_at: datetime
    status: str
    created_at: datetime
