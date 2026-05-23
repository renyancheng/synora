from __future__ import annotations

import json
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx
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


def send_email_notification(db: Session, audit_id: int) -> NotificationAudit:
    settings = get_settings()
    audit = db.get(NotificationAudit, audit_id)
    if not audit:
        raise ValueError("通知审计记录不存在。")

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
        audit.retry_count += 1
    db.commit()
    db.refresh(audit)
    return audit


def send_wecom_robot_notification(db: Session, audit_id: int) -> NotificationAudit:
    settings = get_settings()
    audit = db.get(NotificationAudit, audit_id)
    if not audit:
        raise ValueError("通知审计记录不存在。")

    payload = json.loads(audit.payload_json)
    if not settings.wecom_robot_webhook:
        audit.status = "failed"
        audit.error_message = "未配置企业微信群机器人 Webhook。"
        audit.retry_count += 1
        db.commit()
        db.refresh(audit)
        return audit

    try:
        response = httpx.post(
            settings.wecom_robot_webhook,
            json={
                "msgtype": "markdown",
                "markdown": {"content": payload.get("markdown", payload.get("body", ""))},
            },
            timeout=15,
        )
        response.raise_for_status()
        audit.status = "delivered"
        audit.delivered_at = datetime.now(timezone.utc)
        audit.external_id = f"wecom-{audit.id}"
    except Exception as exc:
        audit.status = "failed"
        audit.error_message = str(exc)
        audit.retry_count += 1
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
    body = (
        f"提醒事项：{schedule.title}\n"
        f"时间：{schedule.scheduled_at.astimezone(timezone.utc).isoformat()}\n"
        f"地点：{schedule.location or '未填写'}\n"
        f"备注：{schedule.details}"
    )
    markdown = (
        f"**Synora 日程提醒**\n"
        f"> 事项：{schedule.title}\n"
        f"> 时间：{schedule.scheduled_at.astimezone(timezone.utc).isoformat()}\n"
        f"> 地点：{schedule.location or '未填写'}\n"
        f"> 备注：{schedule.details}"
    )
    return {
        "title": schedule.title,
        "body": body,
        "markdown": markdown,
    }


def dispatch_notification_core(*, db: Session, reminder_job_id: int) -> dict:
    settings = get_settings()
    job = db.get(ReminderJob, reminder_job_id)
    if not job:
        raise ValueError("提醒任务不存在。")
    schedule = db.get(Schedule, job.schedule_id)
    if not schedule:
        raise ValueError("提醒任务关联的日程不存在。")

    recipient = settings.notification_to_email if job.channel == "email" else "企业微信群机器人"
    provider = "smtp" if job.channel == "email" else "wecom_robot"
    payload = build_notification_payload(schedule)
    audit = queue_notification_audit(
        db,
        user_id=schedule.user_id,
        reminder_job_id=job.id,
        channel=job.channel,
        provider=provider,
        subject=f"Synora 提醒：{schedule.title}",
        recipient=recipient,
        payload=payload,
    )
    if job.channel == "email":
        audit = send_email_notification(db, audit.id)
    else:
        audit = send_wecom_robot_notification(db, audit.id)

    if audit.status == "delivered":
        mark_job_status(db, job, "sent")
    else:
        mark_job_status(db, job, "failed", audit.error_message)

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
