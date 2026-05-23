from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ReminderJob, Schedule
from app.runtime import get_runtime_executor
from app.runtime.approval_gate import ApprovalGate
from app.schemas.schedule import ConflictCheckResponse, ConflictItem, ConflictSuggestion, ScheduleDraft, ScheduleDraftInput
from app.security import sha256_text


def build_draft_hash(draft: ScheduleDraft) -> str:
    payload = draft.model_dump_json(exclude_none=False, by_alias=True)
    return sha256_text(payload)


def create_schedule_draft(db: Session, user_id: int, payload: ScheduleDraftInput) -> tuple[ScheduleDraft, str, list[str], list[str], list[str], float]:
    result = get_runtime_executor().execute_workflow(
        db,
        user_id=user_id,
        workflow="schedule_intake",
        payload=payload.model_dump(mode="json"),
    )
    draft = ScheduleDraft.model_validate(result["draft"])
    draft_hash = build_draft_hash(draft)
    return (
        draft,
        draft_hash,
        list(result.get("missing_fields", [])),
        list(result.get("ambiguity_flags", [])),
        list(result.get("evidence_digest", draft.evidence_digest)),
        float(result.get("parse_confidence", draft.parse_confidence)),
    )


def _schedule_bounds(draft: ScheduleDraft) -> tuple:
    start = draft.scheduled_at
    end = start + timedelta(minutes=draft.duration_minutes or 60)
    return start, end


def detect_conflicts_core(*, db: Session, user_id: int, draft: dict | ScheduleDraft, draft_hash: str | None = None) -> dict:
    schedule_draft = draft if isinstance(draft, ScheduleDraft) else ScheduleDraft.model_validate(draft)
    if not schedule_draft.scheduled_at:
        raise ValueError("缺少日程时间，无法执行冲突检测。")

    start, end = _schedule_bounds(schedule_draft)
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
                    candidate_end=row_end + timedelta(minutes=schedule_draft.duration_minutes),
                )
            )
            candidate_before_start = row.scheduled_at - timedelta(minutes=schedule_draft.duration_minutes)
            suggestions.append(
                ConflictSuggestion(
                    label=f"{row.title} 之前",
                    candidate_start=candidate_before_start,
                    candidate_end=row.scheduled_at,
                )
            )

    return {
        "conflict_items": [item.model_dump(mode="json") for item in items],
        "suggestions": [item.model_dump(mode="json") for item in suggestions[:3]],
        "risk_level": "high" if items else "low",
    }


def detect_conflicts(db: Session, user_id: int, draft: ScheduleDraft, draft_hash: str) -> ConflictCheckResponse:
    canonical_hash = build_draft_hash(draft)
    result = detect_conflicts_core(db=db, user_id=user_id, draft=draft)
    approval_payload = {
        "draft": draft.model_dump(mode="json"),
        "conflicts": result["conflict_items"],
    }
    approval, token = ApprovalGate().create(
        db,
        user_id=user_id,
        action="create_schedule",
        draft_hash=canonical_hash,
        payload=approval_payload,
        normalized_payload=draft.model_dump(mode="json"),
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


def create_schedule_after_approval_core(*, db: Session, user_id: int, approval_token: str, draft: dict | ScheduleDraft) -> dict:
    schedule_draft = draft if isinstance(draft, ScheduleDraft) else ScheduleDraft.model_validate(draft)
    draft_hash = build_draft_hash(schedule_draft)
    ApprovalGate().consume(
        db,
        user_id=user_id,
        action="create_schedule",
        approval_token=approval_token,
        draft_hash=draft_hash,
    )
    if not schedule_draft.scheduled_at or not schedule_draft.reminder_at:
        raise ValueError("日程草稿缺少必要时间字段。")

    schedule = Schedule(
        user_id=user_id,
        title=schedule_draft.title,
        location=schedule_draft.location,
        details=schedule_draft.details,
        source_text=schedule_draft.source_text,
        scheduled_at=schedule_draft.scheduled_at,
        duration_minutes=schedule_draft.duration_minutes,
        reminder_at=schedule_draft.reminder_at,
        source_type=schedule_draft.source_type,
        source_attachment_ids=schedule_draft.source_attachment_ids,
        parse_confidence=schedule_draft.parse_confidence,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    jobs = [
        ReminderJob(
            schedule_id=schedule.id,
            channel="email",
            scheduled_for=schedule_draft.reminder_at,
        ),
        ReminderJob(
            schedule_id=schedule.id,
            channel="wecom_robot",
            scheduled_for=schedule_draft.reminder_at,
        ),
    ]
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


def create_schedule_after_approval(db: Session, user_id: int, approval_token: str, draft: ScheduleDraft) -> tuple[Schedule, list[ReminderJob]]:
    result = create_schedule_after_approval_core(db=db, user_id=user_id, approval_token=approval_token, draft=draft)
    schedule = db.get(Schedule, result["schedule_id"])
    jobs = db.scalars(select(ReminderJob).where(ReminderJob.schedule_id == schedule.id).order_by(ReminderJob.id.asc())).all()
    return schedule, list(jobs)
