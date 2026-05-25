from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.domains.auth.service import ensure_bootstrap_user
from app.domains.notification.service import dispatch_notification_core, get_notification_status_core
from app.domains.quick_note.service import create_quick_note_draft, save_note_after_approval
from app.domains.schedule.service import create_schedule_after_approval, create_schedule_draft, detect_conflicts
from app.schemas.mcp import (
    McpCreateQuickNoteAfterApprovalResult,
    McpCreateScheduleAfterApprovalResult,
    McpDetectScheduleConflictsResult,
    McpDispatchNotificationResult,
    McpGetNotificationStatusResult,
    McpParseScheduleDraftResult,
    McpPrepareQuickNoteDraftResult,
)
from app.schemas.quick_note import QuickNoteDraftRequest
from app.schemas.schedule import ReminderJobInfo, ScheduleDraftInput, ScheduleEventDraft


def _with_db() -> tuple[Session, int]:
    db = SessionLocal()
    user_id = ensure_bootstrap_user(db).id
    return db, user_id


def parse_schedule_draft_tool(
    text_content: str | None = None,
    attachment_ids: list[int] | None = None,
    context: dict[str, str] | None = None,
) -> McpParseScheduleDraftResult:
    """Parse text or attachments into a schedule draft that still needs confirmation."""
    db, user_id = _with_db()
    try:
        draft, draft_hash, missing_fields, ambiguity_flags, evidence_digest, parse_confidence = create_schedule_draft(
            db,
            user_id,
            ScheduleDraftInput(
                text_content=text_content,
                attachment_ids=attachment_ids or [],
                context=context or {},
            ),
        )
        return McpParseScheduleDraftResult(
            status="ok",
            draft=draft,
            draft_hash=draft_hash,
            missing_fields=missing_fields,
            ambiguity_flags=ambiguity_flags,
            evidence_digest=evidence_digest,
            parse_confidence=parse_confidence,
        )
    except ValueError as exc:
        return McpParseScheduleDraftResult(status="error", error_code="business_error", message=str(exc))
    finally:
        db.close()


def detect_schedule_conflicts_tool(
    draft: ScheduleEventDraft,
    draft_hash: str | None = None,
) -> McpDetectScheduleConflictsResult:
    """Check a candidate schedule against existing events and suggest alternatives."""
    db, user_id = _with_db()
    try:
        result = detect_conflicts(db, user_id, draft, draft_hash or "")
        return McpDetectScheduleConflictsResult(
            status=result.status,
            conflict_items=result.conflict_items,
            suggestions=result.suggestions,
            risk_level=result.risk_level,
            approval=result.approval,
        )
    except ValueError as exc:
        return McpDetectScheduleConflictsResult(status="error", error_code="business_error", message=str(exc))
    finally:
        db.close()


def create_schedule_after_approval_tool(
    approval_token: str,
    normalized_draft: ScheduleEventDraft,
) -> McpCreateScheduleAfterApprovalResult:
    """Create the final schedule and reminder jobs after approval_token validation."""
    db, user_id = _with_db()
    try:
        schedule, jobs = create_schedule_after_approval(db, user_id, approval_token, normalized_draft)
        return McpCreateScheduleAfterApprovalResult(
            status="ok",
            schedule_id=schedule.id,
            reminder_jobs=[
                ReminderJobInfo(id=job.id, channel=job.channel, scheduled_for=job.scheduled_for, status=job.status)
                for job in jobs
            ],
        )
    except ValueError as exc:
        return McpCreateScheduleAfterApprovalResult(status="error", error_code="business_error", message=str(exc))
    finally:
        db.close()


def prepare_quick_note_draft_tool(
    content: str,
    tags: list[str] | None = None,
    attachment_ids: list[int] | None = None,
    context: dict[str, str] | None = None,
) -> McpPrepareQuickNoteDraftResult:
    """Prepare a quick note draft and return approval metadata without writing final data."""
    db, user_id = _with_db()
    note_tags = tags or []
    note_attachment_ids = attachment_ids or []
    try:
        normalized_content, preview_tags, token, evidence_digest, approval = create_quick_note_draft(
            db,
            user_id,
            QuickNoteDraftRequest(
                content=content,
                tags=note_tags,
                attachment_ids=note_attachment_ids,
                context=context or {},
            ),
        )
        return McpPrepareQuickNoteDraftResult(
            status="pending_approval",
            normalized_content=normalized_content,
            preview_tags=preview_tags,
            attachment_ids=note_attachment_ids,
            evidence_digest=evidence_digest,
            approval={
                "approval_token": token,
                "action": approval.action,
                "expires_at": approval.expires_at,
                "draft_hash": approval.draft_hash,
            },
        )
    except ValueError as exc:
        return McpPrepareQuickNoteDraftResult(status="error", error_code="business_error", message=str(exc))
    finally:
        db.close()


def create_quick_note_after_approval_tool(
    content: str,
    tags: list[str] | None = None,
    attachment_ids: list[int] | None = None,
    approval_token: str = "",
) -> McpCreateQuickNoteAfterApprovalResult:
    """Create the final quick note after approval_token validation."""
    db, user_id = _with_db()
    note_tags = tags or []
    note_attachment_ids = attachment_ids or []
    try:
        note = save_note_after_approval(
            db,
            user_id,
            content=content,
            tags=note_tags,
            attachment_ids=note_attachment_ids,
            approval_token=approval_token,
        )
        return McpCreateQuickNoteAfterApprovalResult(
            status="ok",
            note_id=note.id,
            topic_tags=list(note.topic_tags_json),
            normalized_content=note.content,
            attachment_ids=note_attachment_ids,
        )
    except ValueError as exc:
        return McpCreateQuickNoteAfterApprovalResult(status="error", error_code="business_error", message=str(exc))
    finally:
        db.close()


def dispatch_notification_tool(reminder_job_id: int) -> McpDispatchNotificationResult:
    """Send a reminder notification for a reminder job and persist audit data."""
    db, _ = _with_db()
    try:
        result = dispatch_notification_core(db=db, reminder_job_id=reminder_job_id)
        return McpDispatchNotificationResult(
            status="ok",
            delivery_id=result["delivery_id"],
            delivery_status=result["status"],
            provider=result["provider"],
        )
    except ValueError as exc:
        return McpDispatchNotificationResult(status="error", error_code="business_error", message=str(exc))
    finally:
        db.close()


def get_notification_status_tool(delivery_id: int) -> McpGetNotificationStatusResult:
    """Get delivery status and retry information for a notification audit record."""
    db, _ = _with_db()
    try:
        result = get_notification_status_core(db=db, delivery_id=delivery_id)
        return McpGetNotificationStatusResult(
            status=result["status"],
            delivery_id=result["delivery_id"],
            channel_status=result["channel_status"],
            retry_info=result["retry_info"],
        )
    except ValueError as exc:
        return McpGetNotificationStatusResult(status="error", error_code="business_error", message=str(exc))
    finally:
        db.close()
