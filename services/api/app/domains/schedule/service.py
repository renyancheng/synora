from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import NotificationAudit, ReminderJob, Schedule
from app.runtime.approval_gate import ApprovalGate
from app.runtime.model_adapter import ModelAdapter
from app.runtime.tool_impls import parse_schedule_draft
from app.schemas.schedule import (
    ConflictCheckResponse,
    ConflictItem,
    ConflictSuggestion,
    ScheduleDraftInput,
    ScheduleEventDraft,
)
from app.security import sha256_text


def build_draft_hash(draft: ScheduleEventDraft) -> str:
    payload = draft.model_dump_json(exclude_none=False, by_alias=True)
    return sha256_text(payload)


def _event_bounds(draft: ScheduleEventDraft) -> tuple[datetime, datetime]:
    start = draft.start.date_time.astimezone(timezone.utc)
    end = draft.end.date_time.astimezone(timezone.utc)
    return start, end


def _event_duration_minutes(draft: ScheduleEventDraft) -> int:
    start, end = _event_bounds(draft)
    return max(1, int((end - start).total_seconds() // 60))


def _format_event_point(dt: datetime, timezone_name: str) -> dict:
    local = dt.astimezone(ZoneInfo(timezone_name))
    return {"dateTime": local.isoformat(), "timeZone": timezone_name}


def _parse_rrule(rule: str) -> dict[str, str]:
    if not rule.startswith("RRULE:"):
        return {}
    parts = rule.removeprefix("RRULE:").split(";")
    values: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.upper()] = value.upper()
    return values


def _expand_occurrences(
    *,
    start_at: datetime,
    end_at: datetime,
    recurrence_rules: list[str],
    horizon_days: int = 180,
) -> list[tuple[datetime, datetime]]:
    if not recurrence_rules:
        return [(start_at, end_at)]

    occurrences = [(start_at, end_at)]
    horizon_end = start_at + timedelta(days=horizon_days)
    for rule in recurrence_rules:
        parsed = _parse_rrule(rule)
        freq = parsed.get("FREQ")
        if freq == "DAILY":
            current_start = start_at
            current_end = end_at
            while True:
                current_start += timedelta(days=1)
                current_end += timedelta(days=1)
                if current_start > horizon_end:
                    break
                occurrences.append((current_start, current_end))
        elif freq == "WEEKLY":
            current_start = start_at
            current_end = end_at
            while True:
                current_start += timedelta(days=7)
                current_end += timedelta(days=7)
                if current_start > horizon_end:
                    break
                occurrences.append((current_start, current_end))
        elif freq == "MONTHLY":
            current_start = start_at
            current_end = end_at
            while True:
                month = current_start.month + 1
                year = current_start.year
                if month > 12:
                    month = 1
                    year += 1
                day = min(current_start.day, 28)
                next_start = current_start.replace(year=year, month=month, day=day)
                next_end = next_start + (current_end - current_start)
                current_start, current_end = next_start, next_end
                if current_start > horizon_end:
                    break
                occurrences.append((current_start, current_end))
    return occurrences


def _build_reminder_jobs(schedule: Schedule) -> list[ReminderJob]:
    now = datetime.now(timezone.utc)
    offsets = schedule.reminder_offsets_minutes_json or [-1440]
    scheduled_times: list[datetime] = []
    for offset in offsets:
        reminder_time = schedule.start_at + timedelta(minutes=int(offset))
        if reminder_time <= now:
            fallback_minutes = -30 if schedule.start_at - timedelta(minutes=30) > now else 5
            reminder_time = schedule.start_at + timedelta(minutes=fallback_minutes)
            if reminder_time <= now:
                reminder_time = now + timedelta(minutes=5)
        scheduled_times.append(reminder_time)

    earliest = min(scheduled_times)
    return [
        ReminderJob(schedule_id=schedule.id, channel="email", scheduled_for=earliest),
        ReminderJob(schedule_id=schedule.id, channel="wecom_robot", scheduled_for=earliest),
    ]


def create_schedule_draft(db: Session, user_id: int, payload: ScheduleDraftInput) -> tuple[ScheduleEventDraft, str, list[str], list[str], list[str], float]:
    result = parse_schedule_draft(
        db=db,
        user_id=user_id,
        text_content=payload.text_content,
        attachment_ids=payload.attachment_ids,
        context=payload.context,
    )
    draft = ScheduleEventDraft.model_validate(result["draft"])
    draft_hash = build_draft_hash(draft)
    return (
        draft,
        draft_hash,
        list(result.get("missing_fields", [])),
        list(result.get("ambiguity_flags", [])),
        list(result.get("evidence_digest", draft.evidence_digest)),
        float(result.get("parse_confidence", draft.parse_confidence)),
    )


def detect_conflicts_core(*, db: Session, user_id: int, draft: dict | ScheduleEventDraft, draft_hash: str | None = None) -> dict:
    del draft_hash
    schedule_draft = draft if isinstance(draft, ScheduleEventDraft) else ScheduleEventDraft.model_validate(draft)
    draft_start, draft_end = _event_bounds(schedule_draft)
    draft_occurrences = _expand_occurrences(
        start_at=draft_start,
        end_at=draft_end,
        recurrence_rules=schedule_draft.recurrence,
    )

    rows = db.scalars(select(Schedule).where(Schedule.user_id == user_id, Schedule.status == "scheduled")).all()
    items: list[ConflictItem] = []
    suggestions: list[ConflictSuggestion] = []

    for row in rows:
        row_occurrences = _expand_occurrences(
            start_at=row.start_at,
            end_at=row.end_at,
            recurrence_rules=list(row.recurrence_rules_json or []),
        )
        conflict_found = False
        for candidate_start, candidate_end in draft_occurrences:
            for row_start, row_end in row_occurrences:
                if candidate_start < row_end and candidate_end > row_start:
                    items.append(
                        ConflictItem(
                            schedule_id=row.id,
                            title=row.title,
                            start=_format_event_point(row_start, row.time_zone),
                            end=_format_event_point(row_end, row.time_zone),
                            location=row.location,
                        )
                    )
                    duration = candidate_end - candidate_start
                    suggestions.append(
                        ConflictSuggestion(
                            label=f"{row.title} 之后",
                            start=_format_event_point(row_end, schedule_draft.start.time_zone),
                            end=_format_event_point(row_end + duration, schedule_draft.end.time_zone),
                        )
                    )
                    before_start = row_start - duration
                    suggestions.append(
                        ConflictSuggestion(
                            label=f"{row.title} 之前",
                            start=_format_event_point(before_start, schedule_draft.start.time_zone),
                            end=_format_event_point(row_start, schedule_draft.end.time_zone),
                        )
                    )
                    conflict_found = True
                    break
            if conflict_found:
                break

    return {
        "conflict_items": [item.model_dump(mode="json", by_alias=True) for item in items],
        "suggestions": [item.model_dump(mode="json", by_alias=True) for item in suggestions[:3]],
        "risk_level": "high" if items else "low",
    }


def detect_conflicts(db: Session, user_id: int, draft: ScheduleEventDraft, draft_hash: str) -> ConflictCheckResponse:
    canonical_hash = build_draft_hash(draft)
    result = detect_conflicts_core(db=db, user_id=user_id, draft=draft)
    approval_payload = {
        "draft": draft.model_dump(mode="json", by_alias=True),
        "conflicts": result["conflict_items"],
    }
    approval, token = ApprovalGate().create(
        db,
        user_id=user_id,
        action="create_schedule",
        draft_hash=canonical_hash,
        payload=approval_payload,
        normalized_payload=draft.model_dump(mode="json", by_alias=True),
        evidence_digest=draft.evidence_digest,
    )
    return ConflictCheckResponse(
        conflict_items=[ConflictItem.model_validate(item) for item in result["conflict_items"]],
        suggestions=[ConflictSuggestion.model_validate(item) for item in result["suggestions"]],
        risk_level=result["risk_level"],
        approval={
            "approval_token": token,
            "action": "create_schedule",
            "expires_at": approval.expires_at,
            "draft_hash": canonical_hash,
        },
    )


def create_schedule_after_approval_core(*, db: Session, user_id: int, approval_token: str, draft: dict | ScheduleEventDraft) -> dict:
    schedule_draft = draft if isinstance(draft, ScheduleEventDraft) else ScheduleEventDraft.model_validate(draft)
    draft_hash = build_draft_hash(schedule_draft)
    ApprovalGate().consume(
        db,
        user_id=user_id,
        action="create_schedule",
        approval_token=approval_token,
        draft_hash=draft_hash,
    )

    start_at, end_at = _event_bounds(schedule_draft)
    reminder_offsets = ModelAdapter.compute_reminder_offsets(start_at)
    primary_reminder_at = min(start_at + timedelta(minutes=offset) for offset in reminder_offsets)
    schedule = Schedule(
        user_id=user_id,
        title=schedule_draft.title,
        location=schedule_draft.location,
        details=schedule_draft.details,
        source_text=schedule_draft.source_text,
        start_at=start_at,
        end_at=end_at,
        time_zone=schedule_draft.start.time_zone,
        is_all_day=schedule_draft.is_all_day,
        recurrence_rules_json=schedule_draft.recurrence,
        reminder_offsets_minutes_json=reminder_offsets,
        source_attachment_ids=schedule_draft.source_attachment_ids,
        parse_confidence=schedule_draft.parse_confidence,
        scheduled_at=start_at,
        duration_minutes=_event_duration_minutes(schedule_draft),
        reminder_at=primary_reminder_at,
        source_type="mixed",
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    jobs = _build_reminder_jobs(schedule)
    db.add_all(jobs)
    db.commit()
    for job in jobs:
        db.refresh(job)
    return {
        "schedule_id": schedule.id,
        "reminder_jobs": [
            {"id": job.id, "channel": job.channel, "scheduled_for": job.scheduled_for.isoformat(), "status": job.status}
            for job in jobs
        ],
    }


def create_schedule_after_approval(db: Session, user_id: int, approval_token: str, draft: ScheduleEventDraft) -> tuple[Schedule, list[ReminderJob]]:
    result = create_schedule_after_approval_core(db=db, user_id=user_id, approval_token=approval_token, draft=draft)
    schedule = db.get(Schedule, result["schedule_id"])
    jobs = db.scalars(select(ReminderJob).where(ReminderJob.schedule_id == schedule.id).order_by(ReminderJob.id.asc())).all()
    return schedule, list(jobs)


def delete_schedule(db: Session, user_id: int, schedule_id: int) -> None:
    schedule = db.scalar(select(Schedule).where(Schedule.id == schedule_id, Schedule.user_id == user_id))
    if not schedule:
        raise ValueError("日程不存在或已被删除。")

    reminder_job_ids = db.scalars(select(ReminderJob.id).where(ReminderJob.schedule_id == schedule.id)).all()
    if reminder_job_ids:
        db.execute(delete(NotificationAudit).where(NotificationAudit.reminder_job_id.in_(list(reminder_job_ids))))
    db.execute(delete(ReminderJob).where(ReminderJob.schedule_id == schedule.id))
    db.delete(schedule)
    db.commit()
