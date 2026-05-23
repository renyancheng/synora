from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SelectedTool = Literal["schedule", "quick_note"]


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


class EventDateTimeValue(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date_time: datetime = Field(alias="dateTime")
    time_zone: str = Field(alias="timeZone")
