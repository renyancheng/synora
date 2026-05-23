from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings


class OutputNormalizer:
    @staticmethod
    def parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def infer_datetime_from_text(
        text: str,
        *,
        timezone_name: str | None = None,
        reference_time: datetime | None = None,
    ) -> tuple[datetime | None, bool]:
        if not text.strip():
            return None, False

        tz_name = timezone_name or get_settings().default_timezone
        tz = ZoneInfo(tz_name)
        now = reference_time.astimezone(tz) if reference_time else datetime.now(tz)
        normalized = (
            text.replace("：", ":")
            .replace("（", "(")
            .replace("）", ")")
            .replace("，", ",")
        )

        day = None
        if "今天" in normalized:
            day = now.date()
        elif "明天" in normalized:
            day = (now + timedelta(days=1)).date()
        elif "后天" in normalized:
            day = (now + timedelta(days=2)).date()
        else:
            weekday_match = re.search(r"(本周|这周|下周)([一二三四五六日天])", normalized)
            if weekday_match:
                prefix, weekday_text = weekday_match.groups()
                weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
                target = weekday_map[weekday_text]
                current = now.weekday()
                delta = target - current
                if prefix == "下周":
                    delta += 7 if delta <= 0 else 7
                elif delta < 0:
                    delta += 7
                day = (now + timedelta(days=delta)).date()
            else:
                full_date_match = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日", normalized)
                if full_date_match:
                    year_text, month_text, day_text = full_date_match.groups()
                    year = int(year_text) if year_text else now.year
                    month = int(month_text)
                    day_number = int(day_text)
                    try:
                        candidate = datetime(year, month, day_number, tzinfo=tz)
                    except ValueError:
                        candidate = None
                    if candidate is not None and not year_text and candidate.date() < now.date():
                        candidate = datetime(year + 1, month, day_number, tzinfo=tz)
                    if candidate is not None:
                        day = candidate.date()

        if day is None:
            return None, False

        hour = None
        minute = 0
        precise = False

        hm_match = re.search(r"(\d{1,2})[:：](\d{1,2})", normalized)
        if hm_match:
            hour = int(hm_match.group(1))
            minute = int(hm_match.group(2))
            precise = True
        else:
            half_match = re.search(r"(\d{1,2})点半", normalized)
            if half_match:
                hour = int(half_match.group(1))
                minute = 30
                precise = True
            else:
                full_match = re.search(r"(\d{1,2})点(?:(\d{1,2})分?)?", normalized)
                if full_match:
                    hour = int(full_match.group(1))
                    minute = int(full_match.group(2) or 0)
                    precise = True

        if hour is None:
            return datetime(day.year, day.month, day.day, 9, 0, tzinfo=tz), False

        if any(token in normalized for token in ("下午", "晚上", "傍晚")) and hour < 12:
            hour += 12
        elif "中午" in normalized and 1 <= hour <= 10:
            hour += 12
        elif "凌晨" in normalized and hour == 12:
            hour = 0

        inferred = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        return inferred, precise

    @staticmethod
    def coerce_string_list(items: object) -> list[str]:
        if not isinstance(items, list):
            return []
        return [str(item).strip() for item in items if str(item).strip()]
