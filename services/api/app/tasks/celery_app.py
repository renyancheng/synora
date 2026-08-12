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
    },
    "scan-pending-draft-timeouts": {
        "task": "app.tasks.pending.scan_pending_draft_timeouts",
        "schedule": 300.0,
    },
    "scan-pending-intents": {
        "task": "app.tasks.pending.scan_pending_intents",
        "schedule": 60.0,
    },
}
celery_app.conf.timezone = settings.default_timezone

# Ensure task modules are imported so Celery workers register them.
from app.tasks import notifications as _notifications  # noqa: E402,F401
from app.tasks import memory as _memory  # noqa: E402,F401
from app.tasks import pending as _pending  # noqa: E402,F401
