from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.schemas.schedule import ScheduleDraft


@dataclass
class ParsedDraftResult:
    draft: ScheduleDraft
    missing_fields: list[str]
    ambiguity_flags: list[str]


def _parse_datetime(text: str) -> tuple[datetime | None, list[str]]:
    settings = get_settings()
    tz = ZoneInfo(settings.default_timezone)
    flags: list[str] = []

    relative_patterns = [
        ("后天", 2),
        ("明天", 1),
        ("今天", 0),
    ]
    time_match = re.search(r"(?P<hour>\d{1,2})[:点](?P<minute>\d{1,2})?", text)
    for keyword, delta_days in relative_patterns:
        if keyword in text and time_match:
            hour = int(time_match.group("hour"))
            minute = int(time_match.group("minute") or "0")
            base = datetime.now(tz).date() + timedelta(days=delta_days)
            local_dt = datetime(base.year, base.month, base.day, hour, minute, tzinfo=tz)
            return local_dt.astimezone(timezone.utc), flags

    absolute_patterns = [
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H点%M",
        "%Y年%m月%d日 %H:%M",
    ]
    compact_match = re.search(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?\s+\d{1,2}[:点]\d{0,2})", text)
    if compact_match:
        candidate = compact_match.group(1).replace("年", "-").replace("月", "-").replace("日", "")
        candidate = re.sub(r"点(\d{0,2})$", lambda item: f":{item.group(1) or '00'}", candidate)
        for pattern in absolute_patterns:
            try:
                local_dt = datetime.strptime(candidate, pattern.replace("年", "-").replace("月", "-").replace("日", ""))
                return local_dt.replace(tzinfo=tz).astimezone(timezone.utc), flags
            except ValueError:
                continue

    short_match = re.search(r"(\d{1,2})[-/月](\d{1,2})(?:日)?\s+(\d{1,2})[:点](\d{0,2})", text)
    if short_match:
        now = datetime.now(tz)
        month, day, hour, minute = [int(part or "0") for part in short_match.groups()]
        local_dt = datetime(now.year, month, day, hour, minute, tzinfo=tz)
        flags.append("year_inferred")
        return local_dt.astimezone(timezone.utc), flags

    return None, flags


def _extract_location(text: str) -> str | None:
    patterns = [
        r"地点[:：]\s*(.+)",
        r"在([^\n，。,；;]{2,40})",
        r"@([^\n，。,；;]{2,40})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def _extract_title(text: str) -> str:
    for line in [item.strip() for item in text.splitlines() if item.strip()]:
        if line.startswith(("事项:", "事项：", "主题:", "主题：")):
            return line.split(":", 1)[-1].split("：", 1)[-1].strip()
    first_line = text.strip().splitlines()[0].strip()
    first_line = re.sub(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}.*", "", first_line).strip(" -:：,，")
    return first_line[:40] if first_line else "待确认事项"


def compute_reminder_at(scheduled_at: datetime) -> datetime:
    now = datetime.now(timezone.utc)
    reminder_at = scheduled_at - timedelta(days=1)
    if reminder_at <= now:
        reminder_at = scheduled_at - timedelta(minutes=30)
    if reminder_at <= now:
        reminder_at = now + timedelta(minutes=5)
    return reminder_at


def parse_schedule_draft(input_text: str) -> ParsedDraftResult:
    normalized_text = input_text.strip()
    scheduled_at, flags = _parse_datetime(normalized_text)
    title = _extract_title(normalized_text)
    location = _extract_location(normalized_text)
    details = normalized_text
    missing_fields: list[str] = []
    if not scheduled_at:
        missing_fields.append("scheduled_at")
    if not title:
        missing_fields.append("title")
        title = "待确认事项"

    draft = ScheduleDraft(
        title=title,
        location=location,
        details=details,
        source_text=normalized_text,
        scheduled_at=scheduled_at,
        duration_minutes=60,
        reminder_at=compute_reminder_at(scheduled_at) if scheduled_at else None,
    )
    return ParsedDraftResult(draft=draft, missing_fields=missing_fields, ambiguity_flags=flags)


def suggest_note_tags(content: str, tags: list[str]) -> list[str]:
    base_tags = {tag.strip() for tag in tags if tag.strip()}
    heuristics = {
        "教学": ["课程", "学生", "作业", "上课", "答疑"],
        "科研": ["论文", "实验", "项目", "评审", "投稿"],
        "生活": ["家里", "采购", "出行", "孩子", "医院"],
        "会议": ["会议", "讨论", "汇报", "组会"],
    }
    for candidate, keywords in heuristics.items():
        if any(keyword in content for keyword in keywords):
            base_tags.add(candidate)
    if not base_tags:
        base_tags.add("待整理")
    return sorted(base_tags)
