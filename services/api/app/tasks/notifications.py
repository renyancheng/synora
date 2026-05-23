from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.domains.notification.service import (
    build_notification_payload,
    collect_due_jobs,
    mark_job_status,
    queue_notification_audit,
    send_email_notification,
    send_wecom_mock_notification,
)
from app.models import ReminderJob, Schedule
from app.tasks.celery_app import celery_app


def _get_schedule(db: Session, job: ReminderJob) -> Schedule:
    schedule = db.get(Schedule, job.schedule_id)
    if not schedule:
        raise ValueError("提醒任务关联日程不存在")
    return schedule


@celery_app.task(name="app.tasks.notifications.scan_due_reminders")
def scan_due_reminders() -> int:
    db = SessionLocal()
    try:
        jobs = collect_due_jobs(db)
        for job in jobs:
            mark_job_status(db, job, "queued")
            dispatch_notification.delay(job.id)
        return len(jobs)
    finally:
        db.close()


@celery_app.task(name="app.tasks.notifications.dispatch_notification")
def dispatch_notification(reminder_job_id: int) -> str:
    db = SessionLocal()
    try:
        job = db.get(ReminderJob, reminder_job_id)
        if not job:
            return "missing"
        schedule = _get_schedule(db, job)
        payload = build_notification_payload(schedule)
        audit = queue_notification_audit(
            db,
            user_id=schedule.user_id,
            reminder_job_id=job.id,
            channel=job.channel,
            subject=f"Synora 提醒：{schedule.title}",
            payload=payload,
        )
        if job.channel == "email":
            audit = send_email_notification(db, audit.id)
        else:
            audit = send_wecom_mock_notification(db, audit.id)
        if audit.status == "delivered":
            mark_job_status(db, job, "sent")
            return "sent"
        mark_job_status(db, job, "failed", audit.error_message)
        return "failed"
    except Exception as exc:
        if "job" in locals() and job is not None:
            mark_job_status(db, job, "failed", str(exc))
        return "failed"
    finally:
        db.close()
