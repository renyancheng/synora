from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.domains.attachment.service import build_attachment_summaries
from app.runtime.context_assembler import ContextAssembler
from app.runtime.model_adapter import ModelAdapter
from app.runtime.output_normalizer import OutputNormalizer


def _fallback_schedule_parse(merged_text: str, source_type: str, attachment_ids: list[int], timezone_name: str) -> dict:
    now = datetime.now(ZoneInfo(timezone_name))
    scheduled_at, precise = OutputNormalizer.infer_datetime_from_text(
        merged_text,
        timezone_name=timezone_name,
        reference_time=now,
    )
    title = merged_text.splitlines()[0].strip()[:30] if merged_text.strip() else "待确认事项"
    details = merged_text.strip()[:500] or "待补充详情"
    evidence = [line.strip() for line in merged_text.splitlines() if line.strip()][:3] or ["根据输入内容生成"]
    reminder_at = ModelAdapter.compute_reminder_at(scheduled_at) if scheduled_at else None
    return {
        "title": title,
        "location": None,
        "details": details,
        "source_text": merged_text,
        "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        "duration_minutes": 60,
        "reminder_at": reminder_at.isoformat() if reminder_at else None,
        "source_type": source_type,
        "source_attachment_ids": attachment_ids,
        "parse_confidence": 0.35,
        "evidence_digest": evidence,
        "missing_fields": [] if scheduled_at else ["scheduled_at"],
        "ambiguity_flags": [] if precise else (["time_ambiguous"] if scheduled_at is None else []),
    }


def parse_schedule_draft(
    *,
    db: Session,
    user_id: int,
    source_type: str,
    text_content: str | None = None,
    attachment_ids: list[int] | None = None,
    context: dict | None = None,
) -> dict:
    attachment_ids = attachment_ids or []
    context = context or {}
    timezone_name = str(context.get("client_timezone") or get_settings().default_timezone)
    reference_time = datetime.now(ZoneInfo(timezone_name))
    attachment_summaries = build_attachment_summaries(db, user_id=user_id, attachment_ids=attachment_ids)
    assembled = ContextAssembler().build_schedule_context(
        source_type=source_type,
        text_content=text_content or "",
        attachment_summaries=attachment_summaries,
    )
    model = ModelAdapter()
    try:
        parsed = model.extract_schedule(
            merged_text=assembled["merged_text"],
            source_type=source_type,
            timezone_name=timezone_name,
            reference_time=reference_time,
        )
    except Exception:
        parsed = _fallback_schedule_parse(assembled["merged_text"], source_type, attachment_ids, timezone_name)

    normalized = OutputNormalizer()
    scheduled_at = normalized.parse_datetime(parsed.get("scheduled_at"))
    inferred_precise = False
    if not scheduled_at:
        scheduled_at, inferred_precise = normalized.infer_datetime_from_text(
            assembled["merged_text"],
            timezone_name=timezone_name,
            reference_time=reference_time,
        )
    reminder_at = ModelAdapter.compute_reminder_at(scheduled_at) if scheduled_at else None
    evidence_digest = normalized.coerce_string_list(parsed.get("evidence_digest")) or ["根据输入内容自动提取"]
    missing_fields = normalized.coerce_string_list(parsed.get("missing_fields"))
    ambiguity_flags = normalized.coerce_string_list(parsed.get("ambiguity_flags"))
    if scheduled_at:
        missing_fields = [field for field in missing_fields if field != "scheduled_at"]
        if inferred_precise:
            ambiguity_flags = [flag for flag in ambiguity_flags if flag != "time_ambiguous"]
    return {
        "draft": {
            "title": str(parsed.get("title") or "待确认事项").strip()[:30],
            "location": parsed.get("location") or None,
            "details": str(parsed.get("details") or assembled["merged_text"] or "待补充详情").strip(),
            "source_text": assembled["merged_text"],
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
            "duration_minutes": int(parsed.get("duration_minutes") or 60),
            "reminder_at": reminder_at.isoformat() if reminder_at else None,
            "source_type": source_type,
            "source_attachment_ids": attachment_ids,
            "parse_confidence": float(parsed.get("parse_confidence") or 0.0),
            "evidence_digest": evidence_digest,
        },
        "missing_fields": missing_fields,
        "ambiguity_flags": ambiguity_flags,
        "evidence_digest": evidence_digest,
        "parse_confidence": float(parsed.get("parse_confidence") or 0.0),
    }


def _fallback_quick_note(merged_text: str, manual_tags: list[str], source_type: str, attachment_ids: list[int]) -> dict:
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
    evidence = [line.strip() for line in merged_text.splitlines() if line.strip()][:3] or ["根据输入内容自动整理"]
    return {
        "normalized_content": merged_text.strip(),
        "preview_tags": sorted(tags),
        "evidence_digest": evidence,
        "source_type": source_type,
        "attachment_ids": attachment_ids,
    }


def record_quick_note(
    *,
    db: Session,
    user_id: int,
    source_type: str,
    text_content: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    attachment_ids: list[int] | None = None,
    context: dict | None = None,
) -> dict:
    attachment_ids = attachment_ids or []
    tags = tags or []
    attachment_summaries = build_attachment_summaries(db, user_id=user_id, attachment_ids=attachment_ids)
    merged_text = content or text_content or ""
    assembled = ContextAssembler().build_quick_note_context(
        source_type=source_type,
        text_content=merged_text,
        attachment_summaries=attachment_summaries,
        manual_tags=tags,
    )
    model = ModelAdapter()
    try:
        parsed = model.suggest_quick_note_tags(
            merged_text=str(assembled["merged_text"]),
            manual_tags=tags,
            source_type=source_type,
        )
    except Exception:
        parsed = _fallback_quick_note(str(assembled["merged_text"]), tags, source_type, attachment_ids)

    normalized = OutputNormalizer()
    preview_tags = normalized.coerce_string_list(parsed.get("preview_tags"))
    evidence_digest = normalized.coerce_string_list(parsed.get("evidence_digest")) or ["根据输入内容自动整理"]
    return {
        "normalized_content": str(parsed.get("normalized_content") or assembled["merged_text"]).strip(),
        "preview_tags": preview_tags,
        "source_type": source_type,
        "attachment_ids": attachment_ids,
        "evidence_digest": evidence_digest,
    }
