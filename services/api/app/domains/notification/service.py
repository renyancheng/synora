from __future__ import annotations

import json
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import NotificationAudit, ReminderJob, Schedule


def queue_notification_audit(
    db: Session,
    *,
    user_id: int,
    reminder_job_id: int | None,
    channel: str,
    subject: str,
    payload: dict,
) -> NotificationAudit:
    settings = get_settings()
    audit = NotificationAudit(
        user_id=user_id,
        reminder_job_id=reminder_job_id,
        channel=channel,
        recipient=settings.notification_to_email if channel == "email" else "wecom:mock",
        subject=subject,
        payload_json=json.dumps(payload, ensure_ascii=False),
        status="queued",
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


def send_email_notification(db: Session, audit_id: int) -> NotificationAudit:
    settings = get_settings()
    audit = db.get(NotificationAudit, audit_id)
    if not audit:
        raise ValueError("通知审计记录不存在")

    payload = json.loads(audit.payload_json)
    message = EmailMessage()
    message["From"] = settings.notification_from_email
    message["To"] = audit.recipient
    message["Subject"] = audit.subject
    message.set_content(payload.get("body", ""))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        audit.status = "delivered"
        audit.delivered_at = datetime.now(timezone.utc)
        audit.external_id = f"email-{audit.id}"
    except Exception as exc:
        audit.status = "failed"
        audit.error_message = str(exc)
    db.commit()
    db.refresh(audit)
    return audit


def send_wecom_mock_notification(db: Session, audit_id: int) -> NotificationAudit:
    audit = db.get(NotificationAudit, audit_id)
    if not audit:
        raise ValueError("通知审计记录不存在")
    audit.status = "delivered"
    audit.delivered_at = datetime.now(timezone.utc)
    audit.external_id = f"wecom-mock-{audit.id}"
    db.commit()
    db.refresh(audit)
    return audit


def collect_due_jobs(db: Session) -> list[ReminderJob]:
    now = datetime.now(timezone.utc)
    return db.scalars(
        select(ReminderJob).where(ReminderJob.status == "pending", ReminderJob.scheduled_for <= now).order_by(ReminderJob.scheduled_for.asc())
    ).all()


def mark_job_status(db: Session, job: ReminderJob, status: str, error_message: str | None = None) -> ReminderJob:
    job.status = status
    job.last_error = error_message
    if status in {"queued", "failed"}:
        job.attempts += 1
    db.commit()
    db.refresh(job)
    return job


def build_notification_payload(schedule: Schedule) -> dict[str, str]:
    location = f" @ {schedule.location}" if schedule.location else ""
    return {
        "title": schedule.title,
        "body": f"提醒事项：{schedule.title}\n时间：{schedule.scheduled_at.isoformat()}\n地点：{schedule.location or '待补充'}\n备注：{schedule.details}",
        "location": location,
    }
