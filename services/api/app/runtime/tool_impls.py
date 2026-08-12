from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.agent.llm import extract_schedule, suggest_quick_note_tags
from app.config import get_settings
from app.domains.attachment.service import build_attachment_prompt_assets
from app.domains.memory.service import MemoryService
from app.runtime.context_assembler import ContextAssembler
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
    source_history = [item for item in list(context.get("source_history") or []) if isinstance(item, str)]
    previous_draft_summary = str(context.get("previous_draft_summary") or "").strip()
    conversation_history_lines = [item for item in list(context.get("conversation_history_lines") or []) if isinstance(item, str)]

    assets = build_attachment_prompt_assets(db, user_id=user_id, attachment_ids=attachment_ids)
    attachment_parts = _flatten_attachment_parts(assets)
    attachment_texts = _extract_attachment_texts(assets)
    memory_context = MemoryService().retrieve_context(
        db,
        user_id=user_id,
        query_text=(text_content or "").strip() or "\n".join(attachment_texts),
    )
    assembled = ContextAssembler().build_schedule_context(
        text_content=text_content or "",
        attachment_texts=attachment_texts,
        memory_summary=memory_context.summary,
        memory_items=memory_context.items,
        source_history=source_history,
        previous_draft_summary=previous_draft_summary,
        conversation_history_lines=conversation_history_lines,
    )

    parsed = extract_schedule(
        get_settings(),
        merged_text=assembled["prompt_text"],
        attachment_parts=attachment_parts,
        timezone_name=timezone_name,
        reference_time=reference_time,
    )

    normalized = OutputNormalizer()
    start_dt = normalized.parse_datetime(parsed.get("start_at"))
    end_dt = normalized.parse_datetime(parsed.get("end_at"))
    inferred_precise = False

    if not start_dt:
        start_dt, inferred_precise = normalized.infer_datetime_from_text(
            assembled["merged_text"],
            timezone_name=timezone_name,
            reference_time=reference_time,
        )
    if start_dt and not end_dt:
        end_dt = start_dt + timedelta(hours=1)

    missing_fields = normalized.coerce_string_list(parsed.get("missing_fields"))
    ambiguity_flags = normalized.coerce_string_list(parsed.get("ambiguity_flags"))
    evidence_digest = normalized.coerce_string_list(parsed.get("evidence_digest")) or ["根据当前输入自动提取"]

    if start_dt:
        missing_fields = [field for field in missing_fields if field not in {"start_at", "scheduled_at"}]
    elif "start_at" not in missing_fields:
        missing_fields.append("start_at")

    if end_dt:
        missing_fields = [field for field in missing_fields if field != "end_at"]
    elif "end_at" not in missing_fields:
        missing_fields.append("end_at")

    if inferred_precise:
        ambiguity_flags = [field for field in ambiguity_flags if field != "time_ambiguous"]

    if start_dt and end_dt:
        start = _make_event_datetime(start_dt, timezone_name)
        end = _make_event_datetime(end_dt, timezone_name)
    else:
        fallback_start = reference_time
        fallback_end = reference_time + timedelta(hours=1)
        start = _make_event_datetime(fallback_start, timezone_name)
        end = _make_event_datetime(fallback_end, timezone_name)

    return {
        "draft": {
            "title": str(parsed.get("title") or "待确认事项").strip()[:30],
            "location": parsed.get("location") or None,
            "details": str(parsed.get("details") or "待补充详情").strip(),
            "source_text": assembled["source_text"],
            "isAllDay": bool(parsed.get("isAllDay") or parsed.get("is_all_day") or False),
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


def prepare_quick_note_draft(
    *,
    db: Session,
    user_id: int,
    text_content: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    attachment_ids: list[int] | None = None,
    context: dict | None = None,
) -> dict:
    context = context or {}
    attachment_ids = attachment_ids or []
    tags = tags or []

    assets = build_attachment_prompt_assets(db, user_id=user_id, attachment_ids=attachment_ids)
    attachment_parts = _flatten_attachment_parts(assets)
    attachment_texts = _extract_attachment_texts(assets)
    assembled = ContextAssembler().build_quick_note_context(
        text_content=content or text_content or "",
        attachment_texts=attachment_texts,
        manual_tags=tags,
        previous_note_content=str(context.get("previous_note_content") or ""),
        latest_user_text=str(context.get("latest_user_text") or ""),
        conversation_history_lines=[
            item for item in list(context.get("conversation_history_lines") or []) if isinstance(item, str)
        ],
    )
    parsed = suggest_quick_note_tags(
        get_settings(),
        merged_text=str(assembled["prompt_text"]),
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
