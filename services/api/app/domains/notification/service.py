from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domains.notification.fcm import send_system_push
from app.models import NotificationAudit, ReminderJob, Schedule


def _finalize_notification_audit(db: Session, audit: NotificationAudit) -> NotificationAudit:
    db.commit()
    db.refresh(audit)
    return audit


def _fail_notification_audit(db: Session, audit: NotificationAudit, message: str) -> NotificationAudit:
    audit.status = "failed"
    audit.error_message = message
    audit.retry_count += 1
    return _finalize_notification_audit(db, audit)


def queue_notification_audit(
    db: Session,
    *,
    user_id: int,
    reminder_job_id: int | None,
    channel: str,
    provider: str,
    subject: str,
    recipient: str,
    payload: dict,
) -> NotificationAudit:
    audit = NotificationAudit(
        user_id=user_id,
        reminder_job_id=reminder_job_id,
        channel=channel,
        provider=provider,
        recipient=recipient,
        subject=subject,
        payload_json=json.dumps(payload, ensure_ascii=False),
        status="queued",
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


def collect_due_jobs(db: Session) -> list[ReminderJob]:
    now = datetime.now(timezone.utc)
    return db.scalars(
        select(ReminderJob)
        .where(ReminderJob.status == "pending", ReminderJob.scheduled_for <= now)
        .order_by(ReminderJob.scheduled_for.asc())
    ).all()


def mark_job_status(db: Session, job: ReminderJob, status: str, error_message: str | None = None) -> ReminderJob:
    job.status = status
    job.last_error = error_message
    if status in {"queued", "failed"}:
        job.attempts += 1
    db.commit()
    db.refresh(job)
    return job


def _format_event_time(schedule: Schedule) -> str:
    start = schedule.start_at.astimezone(ZoneInfo(schedule.time_zone))
    end = schedule.end_at.astimezone(ZoneInfo(schedule.time_zone))
    if schedule.is_all_day:
        return f"{start.year}年{start.month:02d}月{start.day:02d}日 全天"
    return (
        f"{start.year}年{start.month:02d}月{start.day:02d}日 {start.hour:02d}:{start.minute:02d}"
        f" - {end.hour:02d}:{end.minute:02d}"
    )


def build_notification_payload(schedule: Schedule) -> dict[str, str]:
    time_label = _format_event_time(schedule)
    location = schedule.location or "未填写"
    body = (
        f"提醒事项：{schedule.title}\n"
        f"时间：{time_label}\n"
        f"地点：{location}\n"
        f"备注：{schedule.details}"
    )
    markdown = (
        f"**Synora 日程提醒**\n"
        f"> 事项：{schedule.title}\n"
        f"> 时间：{time_label}\n"
        f"> 地点：{location}\n"
        f"> 备注：{schedule.details}"
    )
    return {
        "title": schedule.title,
        "body": body,
        "markdown": markdown,
    }


def dispatch_notification_core(*, db: Session, reminder_job_id: int) -> dict:
    job = db.get(ReminderJob, reminder_job_id)
    if not job:
        raise ValueError("提醒任务不存在。")
    schedule = db.get(Schedule, job.schedule_id)
    if not schedule:
        raise ValueError("提醒任务关联的日程不存在。")

    payload = build_notification_payload(schedule)
    audit = queue_notification_audit(
        db,
        user_id=schedule.user_id,
        reminder_job_id=job.id,
        channel="system",
        provider="system",
        subject=f"Synora 提醒：{schedule.title}",
        recipient="system",
        payload=payload,
    )
    # 系统级通知：审计直接标记为已送达（前端轮询消费），FCM 作为增强推送，
    # 让应用彻底关闭时也能收到。FCM 失败不改变 audit 状态（轮询兜底）。
    audit.status = "delivered"
    audit.delivered_at = datetime.now(timezone.utc)
    audit.external_id = f"system-{audit.id}"
    _finalize_notification_audit(db, audit)

    push_error = send_system_push(
        db,
        user_id=schedule.user_id,
        title=payload.get("title", ""),
        body=payload.get("body", ""),
        audit_id=audit.id,
    )
    if push_error:
        audit.error_message = push_error
        _finalize_notification_audit(db, audit)

    mark_job_status(db, job, "sent")
    return {
        "delivery_id": audit.id,
        "status": audit.status,
        "provider": audit.provider,
    }


def get_notification_status_core(*, db: Session, delivery_id: int) -> dict:
    audit = db.get(NotificationAudit, delivery_id)
    if not audit:
        raise ValueError("通知记录不存在。")
    return {
        "delivery_id": audit.id,
        "channel_status": audit.status,
        "retry_info": {"retry_count": audit.retry_count, "error_message": audit.error_message},
        "status": "ok",
    }
