from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.runtime.output_normalizer import OutputNormalizer
from app.schemas.common import EventDateTimeValue
from app.schemas.schedule import ScheduleEventDraft


@dataclass
class ParsedDraftResult:
    draft: ScheduleEventDraft
    missing_fields: list[str]
    ambiguity_flags: list[str]


def parse_schedule_draft(input_text: str) -> ParsedDraftResult:
    timezone_name = get_settings().default_timezone
    reference = datetime.now(ZoneInfo(timezone_name))
    start_at, precise = OutputNormalizer.infer_datetime_from_text(
        input_text,
        timezone_name=timezone_name,
        reference_time=reference,
    )
    if start_at is None:
        start_at = reference
    end_at = start_at + timedelta(hours=1)
    draft = ScheduleEventDraft(
        title=input_text.strip()[:30] or "待确认事项",
        location=None,
        details=input_text.strip() or "待补充详情",
        source_text=input_text.strip(),
        isAllDay=False,
        start=EventDateTimeValue(dateTime=start_at, timeZone=timezone_name),
        end=EventDateTimeValue(dateTime=end_at, timeZone=timezone_name),
        recurrence=[],
        source_attachment_ids=[],
        parse_confidence=0.3,
        evidence_digest=[input_text.strip() or "根据输入生成"],
    )
    missing_fields = [] if precise else ["start_at", "end_at"]
    ambiguity_flags = [] if precise else ["time_ambiguous"]
    return ParsedDraftResult(draft=draft, missing_fields=missing_fields, ambiguity_flags=ambiguity_flags)
