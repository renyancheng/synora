from __future__ import annotations

from app.db import SessionLocal
from app.domains.notification.service import collect_due_jobs, dispatch_notification_core, mark_job_status
from app.models import ReminderJob
from app.tasks.celery_app import celery_app


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
        result = dispatch_notification_core(db=db, reminder_job_id=job.id)
        return result["status"]
    except Exception as exc:
        if "job" in locals() and job is not None:
            mark_job_status(db, job, "failed", str(exc))
        return "failed"
    finally:
        db.close()
