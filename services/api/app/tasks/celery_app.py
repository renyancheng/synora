from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "synora",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.beat_schedule = {
    "scan-due-reminders": {
        "task": "app.tasks.notifications.scan_due_reminders",
        "schedule": 60.0,
    }
}
celery_app.conf.timezone = settings.default_timezone

# Ensure task modules are imported so Celery workers register them.
from app.tasks import notifications as _notifications  # noqa: E402,F401
