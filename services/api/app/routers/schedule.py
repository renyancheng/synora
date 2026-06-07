from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from app.db import get_db
from app.dependencies import get_current_user
from app.domains.schedule.service import (
    confirm_schedule_edit,
    create_schedule_after_approval,
    create_schedule_draft,
    delete_schedule,
    detect_conflicts,
    list_schedules as list_schedule_rows,
    preview_schedule_edit,
)
from app.models import Schedule, User
from app.runtime.errors import LLMServiceError
from app.schemas.common import ApiEnvelope
from app.schemas.schedule import (
    ConflictCheckRequest,
    ConflictCheckResponse,
    ReminderJobInfo,
    ScheduleConfirmRequest,
    ScheduleConfirmResponse,
    ScheduleDraftInput,
    ScheduleDraftResponse,
    ScheduleEditConfirmRequest,
    ScheduleEditConfirmResponse,
    ScheduleEditPreviewRequest,
    ScheduleEditPreviewResponse,
    ScheduleItem,
)

router = APIRouter(prefix="/schedule", tags=["schedule"])


def _event_value(date_time, time_zone: str):
    local = date_time.astimezone(ZoneInfo(time_zone))
    return {"dateTime": local.isoformat(), "timeZone": time_zone}


def _schedule_item(row: Schedule) -> ScheduleItem:
    return ScheduleItem(
        id=row.id,
        title=row.title,
        location=row.location,
        details=row.details,
        source_text=row.source_text,
        isAllDay=row.is_all_day,
        start=_event_value(row.start_at, row.time_zone),
        end=_event_value(row.end_at, row.time_zone),
        recurrence=list(row.recurrence_rules_json or []),
        source_attachment_ids=list(row.source_attachment_ids or []),
        reminder_preset=row.reminder_preset or "previous_day_1700",
        reminder_offsets_minutes=[int(item) for item in (row.reminder_offsets_minutes_json or [])],
        status=row.status,
        created_at=row.created_at,
        parse_confidence=row.parse_confidence,
    )


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
    except LLMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc
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
    except LLMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc


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
    except LLMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc

    return ScheduleConfirmResponse(
        schedule_id=schedule.id,
        reminder_jobs=[
            ReminderJobInfo(id=job.id, channel=job.channel, scheduled_for=job.scheduled_for, status=job.status)
            for job in jobs
        ],
    )


@router.post("/{schedule_id}/edits/preview", response_model=ScheduleEditPreviewResponse)
def preview_schedule_edit_endpoint(
    schedule_id: int,
    payload: ScheduleEditPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScheduleEditPreviewResponse:
    try:
        draft, conflict_result, approval, token = preview_schedule_edit(
            db,
            current_user.id,
            schedule_id=schedule_id,
            draft=payload.draft,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ScheduleEditPreviewResponse(
        schedule_id=schedule_id,
        draft=draft,
        conflict_items=conflict_result["conflict_items"],
        suggestions=conflict_result["suggestions"],
        risk_level=conflict_result["risk_level"],
        approval={
            "approval_token": token,
            "action": "update_schedule",
            "expires_at": approval.expires_at,
            "draft_hash": approval.draft_hash,
        },
    )


@router.post("/{schedule_id}/edits/confirm", response_model=ScheduleEditConfirmResponse)
def confirm_schedule_edit_endpoint(
    schedule_id: int,
    payload: ScheduleEditConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScheduleEditConfirmResponse:
    try:
        schedule, jobs = confirm_schedule_edit(
            db,
            current_user.id,
            schedule_id=schedule_id,
            approval_token=payload.approval_token,
            draft=payload.normalized_draft,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ScheduleEditConfirmResponse(
        schedule=_schedule_item(schedule),
        reminder_jobs=[
            ReminderJobInfo(id=job.id, channel=job.channel, scheduled_for=job.scheduled_for, status=job.status)
            for job in jobs
        ],
    )


@router.get("", response_model=list[ScheduleItem])
def list_schedules(
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ScheduleItem]:
    rows = list_schedule_rows(db, current_user.id, query=q)
    return [_schedule_item(row) for row in rows]


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
