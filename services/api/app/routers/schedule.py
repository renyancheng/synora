from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.domains.schedule.service import create_schedule_after_approval, create_schedule_draft, delete_schedule, detect_conflicts
from app.models import Schedule, User
from app.schemas.common import ApiEnvelope
from app.schemas.schedule import (
    ConflictCheckRequest,
    ConflictCheckResponse,
    ReminderJobInfo,
    ScheduleConfirmRequest,
    ScheduleConfirmResponse,
    ScheduleDraftInput,
    ScheduleDraftResponse,
    ScheduleItem,
)

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.post("/drafts", response_model=ScheduleDraftResponse)
def create_draft(
    payload: ScheduleDraftInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScheduleDraftResponse:
    try:
        draft, draft_hash, missing_fields, ambiguity_flags, evidence_digest, parse_confidence = create_schedule_draft(
            db,
            current_user.id,
            payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ScheduleDraftResponse(
        draft=draft,
        draft_hash=draft_hash,
        missing_fields=missing_fields,
        ambiguity_flags=ambiguity_flags,
        evidence_digest=evidence_digest,
        parse_confidence=parse_confidence,
    )


@router.post("/conflicts", response_model=ConflictCheckResponse)
def check_conflicts(
    payload: ConflictCheckRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConflictCheckResponse:
    try:
        return detect_conflicts(db, current_user.id, payload.draft, payload.draft_hash)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/confirm", response_model=ScheduleConfirmResponse)
def confirm_schedule(
    payload: ScheduleConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScheduleConfirmResponse:
    try:
        schedule, jobs = create_schedule_after_approval(db, current_user.id, payload.approval_token, payload.normalized_draft)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ScheduleConfirmResponse(
        schedule_id=schedule.id,
        reminder_jobs=[
            ReminderJobInfo(id=job.id, channel=job.channel, scheduled_for=job.scheduled_for, status=job.status)
            for job in jobs
        ],
    )


@router.get("", response_model=list[ScheduleItem])
def list_schedules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ScheduleItem]:
    rows = db.scalars(select(Schedule).where(Schedule.user_id == current_user.id).order_by(Schedule.scheduled_at.asc())).all()
    return [
        ScheduleItem(
            id=row.id,
            title=row.title,
            location=row.location,
            details=row.details,
            scheduled_at=row.scheduled_at,
            duration_minutes=row.duration_minutes,
            reminder_at=row.reminder_at,
            status=row.status,
            created_at=row.created_at,
            source_type=row.source_type,
            parse_confidence=row.parse_confidence,
        )
        for row in rows
    ]


@router.delete("/{schedule_id}", response_model=ApiEnvelope)
def delete_schedule_endpoint(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiEnvelope:
    try:
        delete_schedule(db, current_user.id, schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ApiEnvelope(message="日程已删除。")
