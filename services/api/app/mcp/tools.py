from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
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
    McpGetCurrentTimeResult,
    McpGetNotificationStatusResult,
    McpParseScheduleDraftResult,
    McpPrepareQuickNoteDraftResult,
)
from app.schemas.quick_note import QuickNoteDraftRequest
from app.schemas.schedule import ReminderJobInfo, ScheduleDraftInput, ScheduleEventDraft


def _with_db() -> Session:
    return SessionLocal()


def _normalize_context(context: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(context, dict):
        return {}
    normalized: dict[str, object] = {}
    for key, value in context.items():
        if value is None:
            continue
        if isinstance(value, list):
            normalized[str(key)] = [str(item) for item in value if item is not None]
            continue
        if isinstance(value, dict):
            normalized[str(key)] = {str(child_key): value[child_key] for child_key in value}
            continue
        normalized[str(key)] = str(value)
    return normalized


def _resolve_user_id(db: Session, context: dict[str, object] | None) -> int:
    normalized = _normalize_context(context)
    raw_user_id = normalized.get("user_id")
    if raw_user_id is None:
        return ensure_bootstrap_user(db).id
    raw_user_id_text = str(raw_user_id).strip()
    if not raw_user_id_text:
        return ensure_bootstrap_user(db).id
    try:
        user_id = int(raw_user_id_text)
    except ValueError as exc:
        raise ValueError("context.user_id 无效。") from exc
    if user_id <= 0:
        raise ValueError("context.user_id 无效。")
    return user_id


def parse_schedule_draft_tool(
    text_content: str | None = None,
    attachment_ids: list[int] | None = None,
    context: dict[str, object] | None = None,
) -> McpParseScheduleDraftResult:
    """Parse text or attachments into a schedule draft that still needs confirmation."""
    db = _with_db()
    try:
        user_id = _resolve_user_id(db, context)
        draft, draft_hash, missing_fields, ambiguity_flags, evidence_digest, parse_confidence = create_schedule_draft(
            db,
            user_id,
            ScheduleDraftInput(
                text_content=text_content,
                attachment_ids=attachment_ids or [],
                context=_normalize_context(context),
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
    context: dict[str, object] | None = None,
) -> McpDetectScheduleConflictsResult:
    """Check a candidate schedule against existing events and suggest alternatives."""
    db = _with_db()
    try:
        user_id = _resolve_user_id(db, context)
        normalized_context = _normalize_context(context)
        approval_scope = normalized_context.get("approval_scope") or None
        result = detect_conflicts(db, user_id, draft, draft_hash or "", approval_scope=approval_scope)
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
    context: dict[str, object] | None = None,
) -> McpCreateScheduleAfterApprovalResult:
    """Create the final schedule and reminder jobs after approval_token validation."""
    db = _with_db()
    try:
        user_id = _resolve_user_id(db, context)
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
    context: dict[str, object] | None = None,
) -> McpPrepareQuickNoteDraftResult:
    """Prepare a quick note draft and return approval metadata without writing final data."""
    db = _with_db()
    note_tags = tags or []
    note_attachment_ids = attachment_ids or []
    try:
        user_id = _resolve_user_id(db, context)
        normalized_content, preview_tags, token, evidence_digest, approval = create_quick_note_draft(
            db,
            user_id,
            QuickNoteDraftRequest(
                content=content,
                tags=note_tags,
                attachment_ids=note_attachment_ids,
                context=_normalize_context(context),
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
    context: dict[str, object] | None = None,
) -> McpCreateQuickNoteAfterApprovalResult:
    """Create the final quick note after approval_token validation."""
    db = _with_db()
    note_tags = tags or []
    note_attachment_ids = attachment_ids or []
    try:
        user_id = _resolve_user_id(db, context)
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
    db = _with_db()
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
    db = _with_db()
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


_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def get_current_time_tool() -> McpGetCurrentTimeResult:
    """查看当前时间：返回业务时区本地时间、UTC、星期几（无副作用，不依赖 db）。"""
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.default_timezone or "Asia/Shanghai")
        local_now = datetime.now(tz)
        tz_name = str(settings.default_timezone)
    except Exception:
        # 时区配置非法时降级为 UTC，保证工具始终可用。
        local_now = datetime.now(timezone.utc)
        tz_name = "UTC"
    utc_now = datetime.now(timezone.utc)
    return McpGetCurrentTimeResult(
        status="ok",
        local_time=local_now.strftime("%Y-%m-%d %H:%M:%S"),
        timezone=tz_name,
        weekday=_WEEKDAY_CN[local_now.weekday()],
        utc_time=utc_now.strftime("%Y-%m-%d %H:%M:%S"),
        iso=utc_now.isoformat(),
    )
