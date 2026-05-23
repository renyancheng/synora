from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import SourceType


class AgentSessionIntakeRequest(BaseModel):
    preferred_workflow: Literal["auto", "schedule_intake", "quick_note_intake"] = "auto"
    source_type: SourceType
    text_content: str | None = None
    attachment_ids: list[int] = Field(default_factory=list)
    context: dict[str, str] = Field(default_factory=dict)


class AgentSessionIntakeResponse(BaseModel):
    status: str = "ok"
    workflow: str
    result: dict[str, Any]
