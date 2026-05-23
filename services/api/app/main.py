from fastapi import FastAPI

from app.bootstrap import init_db
from app.config import get_settings
from app.routers import approvals, auth, health, notifications, quick_notes, schedule

settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(schedule.router)
app.include_router(quick_notes.router)
app.include_router(approvals.router)
app.include_router(notifications.router)
