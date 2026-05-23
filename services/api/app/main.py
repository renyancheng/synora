from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import init_db
from app.config import get_settings
from app.routers import agent_sessions, approvals, attachments, auth, conversations, health, notifications, quick_notes, schedule

settings = get_settings()
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5000",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5000",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(attachments.router)
app.include_router(agent_sessions.router)
app.include_router(conversations.router)
app.include_router(schedule.router)
app.include_router(quick_notes.router)
app.include_router(approvals.router)
app.include_router(notifications.router)
