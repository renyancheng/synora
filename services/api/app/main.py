import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import init_db
from app.config import get_settings
from app.mcp import create_mcp_exact_route, create_mcp_http_app, get_mcp_server
from app.mcp.server import get_mcp_mount_path
from app.routers import agent_sessions, approvals, attachments, auth, conversations, health, memory, notifications, quick_notes, schedule, users

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    server = get_mcp_server()
    server.streamable_http_app()
    async with server.session_manager.run():
        init_db()
        logger.info(
            "synora_api_starting llm_enabled=%s llm_model=%s llm_base_url=%s llm_enable_thinking=%s",
            bool(settings.llm_api_key),
            settings.llm_model,
            settings.llm_base_url,
            settings.llm_enable_thinking,
        )
        yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_origin_regex=settings.cors_allow_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(attachments.router)
app.include_router(agent_sessions.router)
app.include_router(conversations.router)
app.include_router(memory.router)
app.include_router(schedule.router)
app.include_router(quick_notes.router)
app.include_router(approvals.router)
app.include_router(notifications.router)
app.router.routes.append(create_mcp_exact_route())
app.mount(f"{get_mcp_mount_path()}/", create_mcp_http_app())
