from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.domains.attachment.service import build_attachment_prompt_assets
from app.runtime.context_assembler import ContextAssembler
from app.runtime.model_adapter import ModelAdapter
from app.runtime.output_normalizer import OutputNormalizer


def _flatten_attachment_parts(assets: list) -> list[dict]:
    parts: list[dict] = []
    for asset in assets:
        parts.extend(asset.parts)
    return parts


def _extract_attachment_texts(assets: list) -> list[str]:
    return [asset.raw_text.strip() for asset in assets if asset.raw_text.strip()]


def _make_event_datetime(dt: datetime, timezone_name: str) -> dict:
    local = dt.astimezone(ZoneInfo(timezone_name))
    return {"dateTime": local.isoformat(), "timeZone": timezone_name}


def _fallback_schedule_parse(merged_text: str, attachment_ids: list[int], timezone_name: str) -> dict:
    now = datetime.now(ZoneInfo(timezone_name))
    start_at, precise = OutputNormalizer.infer_datetime_from_text(
        merged_text,
        timezone_name=timezone_name,
        reference_time=now,
    )
    title = merged_text.splitlines()[0].strip()[:30] if merged_text.strip() else "待确认事项"
    details = merged_text.strip()[:500] or "待补充详情"
    evidence = [line.strip() for line in merged_text.splitlines() if line.strip()][:3] or ["根据当前输入生成"]
    if start_at:
        end_at = start_at + timedelta(hours=1)
        start = _make_event_datetime(start_at, timezone_name)
        end = _make_event_datetime(end_at, timezone_name)
    else:
        start = None
        end = None
    return {
        "title": title,
        "location": None,
        "details": details,
        "source_text": merged_text,
        "isAllDay": False,
        "start": start,
        "end": end,
        "recurrence": [],
        "source_attachment_ids": attachment_ids,
        "parse_confidence": 0.35,
        "evidence_digest": evidence,
        "missing_fields": [] if start_at else ["start_at", "end_at"],
        "ambiguity_flags": [] if precise else (["time_ambiguous"] if start_at is None else []),
    }


def parse_schedule_draft(
    *,
    db: Session,
    user_id: int,
    text_content: str | None = None,
    attachment_ids: list[int] | None = None,
    context: dict | None = None,
) -> dict:
    attachment_ids = attachment_ids or []
    context = context or {}
    timezone_name = str(context.get("client_timezone") or get_settings().default_timezone)
    reference_time = datetime.now(ZoneInfo(timezone_name))
    assets = build_attachment_prompt_assets(db, user_id=user_id, attachment_ids=attachment_ids)
    attachment_parts = _flatten_attachment_parts(assets)
    attachment_texts = _extract_attachment_texts(assets)
    assembled = ContextAssembler().build_schedule_context(
        text_content=text_content or "",
        attachment_texts=attachment_texts,
    )
    model = ModelAdapter()
    parsed = model.extract_schedule(
        merged_text=assembled["merged_text"],
        attachment_parts=attachment_parts,
        timezone_name=timezone_name,
        reference_time=reference_time,
    )

    normalized = OutputNormalizer()
    start_dt = None
    end_dt = None
    raw_start = parsed.get("start_at")
    raw_end = parsed.get("end_at")
    if isinstance(raw_start, str):
        start_dt = normalized.parse_datetime(raw_start)
    elif isinstance(parsed.get("start"), dict):
        start_dt = normalized.parse_datetime(parsed["start"].get("dateTime"))
    if isinstance(raw_end, str):
        end_dt = normalized.parse_datetime(raw_end)
    elif isinstance(parsed.get("end"), dict):
        end_dt = normalized.parse_datetime(parsed["end"].get("dateTime"))

    inferred_precise = False
    if not start_dt:
        start_dt, inferred_precise = normalized.infer_datetime_from_text(
            assembled["merged_text"],
            timezone_name=timezone_name,
            reference_time=reference_time,
        )
    if start_dt and not end_dt:
        end_dt = start_dt + timedelta(hours=1)

    evidence_digest = normalized.coerce_string_list(parsed.get("evidence_digest")) or ["根据当前输入自动提取"]
    missing_fields = normalized.coerce_string_list(parsed.get("missing_fields"))
    ambiguity_flags = normalized.coerce_string_list(parsed.get("ambiguity_flags"))

    if start_dt:
        missing_fields = [field for field in missing_fields if field not in {"start_at", "scheduled_at"}]
    else:
        for field_name in ("start_at", "end_at"):
            if field_name not in missing_fields:
                missing_fields.append(field_name)

    if end_dt:
        missing_fields = [field for field in missing_fields if field != "end_at"]
    else:
        if "end_at" not in missing_fields:
            missing_fields.append("end_at")

    if inferred_precise:
        ambiguity_flags = [flag for flag in ambiguity_flags if flag != "time_ambiguous"]

    if start_dt and end_dt:
        start = _make_event_datetime(start_dt, timezone_name)
        end = _make_event_datetime(end_dt, timezone_name)
    else:
        start = {"dateTime": reference_time.isoformat(), "timeZone": timezone_name}
        end = {"dateTime": (reference_time + timedelta(hours=1)).isoformat(), "timeZone": timezone_name}

    return {
        "draft": {
            "title": str(parsed.get("title") or "待确认事项").strip()[:30],
            "location": parsed.get("location") or None,
            "details": str(parsed.get("details") or assembled["merged_text"] or "待补充详情").strip(),
            "source_text": assembled["merged_text"],
            "isAllDay": bool(parsed.get("is_all_day") or parsed.get("isAllDay") or False),
            "start": start,
            "end": end,
            "recurrence": normalized.coerce_string_list(parsed.get("recurrence")),
            "source_attachment_ids": attachment_ids,
            "parse_confidence": float(parsed.get("parse_confidence") or 0.0),
            "evidence_digest": evidence_digest,
        },
        "missing_fields": missing_fields,
        "ambiguity_flags": ambiguity_flags,
        "evidence_digest": evidence_digest,
        "parse_confidence": float(parsed.get("parse_confidence") or 0.0),
    }


def _fallback_quick_note(merged_text: str, manual_tags: list[str], attachment_ids: list[int]) -> dict:
    tags = set(manual_tags)
    keyword_map = {
        "教学": ["课程", "学生", "作业", "答疑"],
        "科研": ["论文", "实验", "项目", "投稿"],
        "会议": ["会议", "讨论", "汇报"],
        "生活": ["采购", "家庭", "孩子", "医院"],
        "待办": [],
    }
    for label, keywords in keyword_map.items():
        if any(keyword in merged_text for keyword in keywords):
            tags.add(label)
    if not tags:
        tags.add("待办")
    evidence = [line.strip() for line in merged_text.splitlines() if line.strip()][:3] or ["根据当前输入自动整理"]
    return {
        "normalized_content": merged_text.strip(),
        "preview_tags": sorted(tags),
        "attachment_ids": attachment_ids,
        "evidence_digest": evidence,
    }


def record_quick_note(
    *,
    db: Session,
    user_id: int,
    text_content: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    attachment_ids: list[int] | None = None,
    context: dict | None = None,
) -> dict:
    del context
    attachment_ids = attachment_ids or []
    tags = tags or []
    assets = build_attachment_prompt_assets(db, user_id=user_id, attachment_ids=attachment_ids)
    attachment_parts = _flatten_attachment_parts(assets)
    attachment_texts = _extract_attachment_texts(assets)
    merged_text = content or text_content or ""
    assembled = ContextAssembler().build_quick_note_context(
        text_content=merged_text,
        attachment_texts=attachment_texts,
        manual_tags=tags,
    )
    model = ModelAdapter()
    parsed = model.suggest_quick_note_tags(
        merged_text=str(assembled["merged_text"]),
        manual_tags=tags,
        attachment_parts=attachment_parts,
    )

    normalized = OutputNormalizer()
    preview_tags = normalized.coerce_string_list(parsed.get("preview_tags"))
    evidence_digest = normalized.coerce_string_list(parsed.get("evidence_digest")) or ["根据当前输入自动整理"]
    return {
        "normalized_content": str(parsed.get("normalized_content") or assembled["merged_text"]).strip(),
        "preview_tags": preview_tags,
        "attachment_ids": attachment_ids,
        "evidence_digest": evidence_digest,
    }
