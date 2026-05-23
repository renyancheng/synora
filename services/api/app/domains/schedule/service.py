from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.approval.service import consume_approval_request, create_approval_request
from app.models import ReminderJob, Schedule
from app.runtime.agent_runtime_stub import parse_schedule_draft
from app.schemas.schedule import ConflictCheckResponse, ConflictItem, ConflictSuggestion, ScheduleDraft
from app.security import sha256_text


def build_draft_hash(draft: ScheduleDraft) -> str:
    payload = draft.model_dump_json(exclude_none=False, by_alias=True)
    return sha256_text(payload)


def parse_schedule(user_id: int, input_text: str) -> tuple[ScheduleDraft, str, list[str], list[str]]:
    result = parse_schedule_draft(input_text)
    draft_hash = build_draft_hash(result.draft)
    return result.draft, draft_hash, result.missing_fields, result.ambiguity_flags


def _schedule_bounds(draft: ScheduleDraft) -> tuple:
    start = draft.scheduled_at
    end = start + timedelta(minutes=draft.duration_minutes or 60)
    return start, end


def detect_conflicts(db: Session, user_id: int, draft: ScheduleDraft, draft_hash: str) -> ConflictCheckResponse:
    if not draft.scheduled_at:
        raise ValueError("缺少日程时间，无法执行冲突检测")
    canonical_hash = build_draft_hash(draft)

    start, end = _schedule_bounds(draft)
    rows = db.scalars(select(Schedule).where(Schedule.user_id == user_id, Schedule.status == "scheduled")).all()
    items: list[ConflictItem] = []
    suggestions: list[ConflictSuggestion] = []
    for row in rows:
        row_end = row.scheduled_at + timedelta(minutes=row.duration_minutes)
        if start < row_end and end > row.scheduled_at:
            items.append(
                ConflictItem(
                    schedule_id=row.id,
                    title=row.title,
                    starts_at=row.scheduled_at,
                    ends_at=row_end,
                    location=row.location,
                )
            )
            suggestions.append(
                ConflictSuggestion(
                    label=f"{row.title} 之后",
                    candidate_start=row_end,
                    candidate_end=row_end + timedelta(minutes=draft.duration_minutes),
                )
            )

    approval_payload = {"draft": draft.model_dump(mode="json"), "conflicts": [item.model_dump(mode="json") for item in items]}
    approval, token = create_approval_request(db, user_id, "create_schedule", approval_payload, canonical_hash)
    return ConflictCheckResponse(
        conflict_items=items,
        suggestions=suggestions[:3],
        risk_level="high" if items else "low",
        approval={
            "approval_token": token,
            "action": "create_schedule",
            "expires_at": approval.expires_at,
            "draft_hash": canonical_hash,
        },
    )


def create_schedule_after_approval(db: Session, user_id: int, approval_token: str, draft: ScheduleDraft) -> tuple[Schedule, list[ReminderJob]]:
    draft_hash = build_draft_hash(draft)
    consume_approval_request(db, user_id, "create_schedule", approval_token, draft_hash)
    if not draft.scheduled_at or not draft.reminder_at:
        raise ValueError("日程草稿缺少必要时间字段")

    schedule = Schedule(
        user_id=user_id,
        title=draft.title,
        location=draft.location,
        details=draft.details,
        source_text=draft.source_text,
        scheduled_at=draft.scheduled_at,
        duration_minutes=draft.duration_minutes,
        reminder_at=draft.reminder_at,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    jobs = [
        ReminderJob(
            schedule_id=schedule.id,
            channel="email",
            scheduled_for=draft.reminder_at,
        ),
        ReminderJob(
            schedule_id=schedule.id,
            channel="wecom_mock",
            scheduled_for=draft.reminder_at,
        ),
    ]
    db.add_all(jobs)
    db.commit()
    for job in jobs:
        db.refresh(job)
    return schedule, jobs
