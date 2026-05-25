from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    id: int
    memory_type: str
    title: str
    content: str
    source_kind: str
    source_ref_id: str | None = None
    is_active: bool
    updated_at: datetime


class MemoryListResponse(BaseModel):
    status: str = "ok"
    summary: str = ""
    items: list[MemoryItem] = Field(default_factory=list)

