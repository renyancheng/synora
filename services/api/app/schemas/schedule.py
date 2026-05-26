from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ApprovalInfo, EventDateTimeValue


class ScheduleDraftInput(BaseModel):
    text_content: str | None = None
    attachment_ids: list[int] = Field(default_factory=list)
    context: dict[str, str] = Field(default_factory=dict)


class ScheduleEventDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    location: str | None = None
    details: str
    source_text: str
    is_all_day: bool = Field(default=False, alias="isAllDay")
    start: EventDateTimeValue
    end: EventDateTimeValue
    recurrence: list[str] = Field(default_factory=list)
    source_attachment_ids: list[int] = Field(default_factory=list)
    parse_confidence: float = 0.0
    evidence_digest: list[str] = Field(default_factory=list)


class ScheduleDraftResponse(BaseModel):
    status: str = "ok"
    draft: ScheduleEventDraft
    draft_hash: str
    missing_fields: list[str]
    ambiguity_flags: list[str]
    evidence_digest: list[str]
    parse_confidence: float


class ConflictCheckRequest(BaseModel):
    draft: ScheduleEventDraft
    draft_hash: str


class ConflictItem(BaseModel):
    schedule_id: int
    title: str
    start: EventDateTimeValue
    end: EventDateTimeValue
    location: str | None = None


class ConflictSuggestion(BaseModel):
    label: str
    start: EventDateTimeValue
    end: EventDateTimeValue


class ConflictCheckResponse(BaseModel):
    status: str = "ok"
    conflict_items: list[ConflictItem]
    suggestions: list[ConflictSuggestion]
    risk_level: str
    approval: ApprovalInfo


class ScheduleConfirmRequest(BaseModel):
    approval_token: str
    normalized_draft: ScheduleEventDraft


class ReminderJobInfo(BaseModel):
    id: int
    channel: str
    scheduled_for: datetime
    status: str


class ScheduleConfirmResponse(BaseModel):
    status: str = "ok"
    schedule_id: int
    reminder_jobs: list[ReminderJobInfo]


class ScheduleEditPreviewRequest(BaseModel):
    draft: ScheduleEventDraft


class ScheduleEditPreviewResponse(BaseModel):
    status: str = "ok"
    schedule_id: int
    draft: ScheduleEventDraft
    conflict_items: list[ConflictItem]
    suggestions: list[ConflictSuggestion]
    risk_level: str
    approval: ApprovalInfo


class ScheduleEditConfirmRequest(BaseModel):
    approval_token: str
    normalized_draft: ScheduleEventDraft


class ScheduleEditConfirmResponse(BaseModel):
    status: str = "ok"
    schedule: "ScheduleItem"
    reminder_jobs: list[ReminderJobInfo]


class ScheduleItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    title: str
    location: str | None = None
    details: str
    source_text: str
    is_all_day: bool = Field(alias="isAllDay")
    start: EventDateTimeValue
    end: EventDateTimeValue
    recurrence: list[str] = Field(default_factory=list)
    source_attachment_ids: list[int] = Field(default_factory=list)
    reminder_offsets_minutes: list[int]
    status: str
    created_at: datetime
    parse_confidence: float
