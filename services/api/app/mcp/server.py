from __future__ import annotations

from functools import lru_cache

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings
from app.mcp.tools import (
    create_quick_note_after_approval_tool,
    create_schedule_after_approval_tool,
    detect_schedule_conflicts_tool,
    dispatch_notification_tool,
    get_notification_status_tool,
    parse_schedule_draft_tool,
    prepare_quick_note_draft_tool,
)


class StaticBearerProtectedApp:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        settings = get_settings()
        expected_token = settings.mcp_bearer_token.strip()
        if not expected_token:
            response = JSONResponse(
                {"detail": "MCP bearer token not configured."},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        if not authorization.lower().startswith("bearer "):
            response = JSONResponse(
                {"detail": "Missing MCP bearer token."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        token = authorization.split(" ", 1)[1].strip()
        if token != expected_token:
            response = JSONResponse(
                {"detail": "Invalid MCP bearer token."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


class ExactPathForwardingApp:
    def __init__(self, app: ASGIApp, mount_path: str) -> None:
        self._app = app
        self._mount_path = mount_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path == self._mount_path:
            forwarded_path = "/"
        elif path.startswith(f"{self._mount_path}/"):
            forwarded_path = path[len(self._mount_path) :] or "/"
        else:
            forwarded_path = path

        forwarded_scope = dict(scope)
        forwarded_scope["path"] = forwarded_path
        forwarded_scope["root_path"] = f"{scope.get('root_path', '')}{self._mount_path}"
        await self._app(forwarded_scope, receive, send)


def _normalize_mount_path(path: str) -> str:
    normalized = (path or "/mcp").strip()
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


@lru_cache
def get_mcp_server() -> FastMCP:
    server = FastMCP(
        name="Synora MCP",
        instructions="Single-user assistant tools for schedules, quick notes, and reminders.",
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    server.add_tool(
        parse_schedule_draft_tool,
        name="parse_schedule_draft",
        description="Parse text or attachments into a schedule draft that still needs confirmation.",
        structured_output=True,
    )
    server.add_tool(
        detect_schedule_conflicts_tool,
        name="detect_schedule_conflicts",
        description="Check a candidate schedule against existing events and suggest alternatives.",
        structured_output=True,
    )
    server.add_tool(
        create_schedule_after_approval_tool,
        name="create_schedule_after_approval",
        description="Create the final schedule and reminder jobs after approval_token validation.",
        structured_output=True,
    )
    server.add_tool(
        prepare_quick_note_draft_tool,
        name="prepare_quick_note_draft",
        description="Prepare a quick note draft and return approval metadata without final write.",
        structured_output=True,
    )
    server.add_tool(
        create_quick_note_after_approval_tool,
        name="create_quick_note_after_approval",
        description="Create the final quick note after approval_token validation.",
        structured_output=True,
    )
    server.add_tool(
        dispatch_notification_tool,
        name="dispatch_notification",
        description="Send a reminder notification for a reminder job and persist audit data.",
        structured_output=True,
    )
    server.add_tool(
        get_notification_status_tool,
        name="get_notification_status",
        description="Get delivery status and retry information for a notification audit record.",
        structured_output=True,
    )
    return server


@lru_cache
def create_mcp_http_app() -> ASGIApp:
    return StaticBearerProtectedApp(get_mcp_server().streamable_http_app())


@lru_cache
def create_mcp_endpoint_app(mount_path: str | None = None) -> ASGIApp:
    normalized_mount_path = _normalize_mount_path(mount_path or get_settings().mcp_mount_path)
    return ExactPathForwardingApp(create_mcp_http_app(), normalized_mount_path)


def create_mcp_exact_route() -> Route:
    mount_path = get_mcp_mount_path()
    return Route(
        mount_path,
        endpoint=create_mcp_endpoint_app(mount_path),
        methods=["GET", "POST", "DELETE"],
        include_in_schema=False,
    )


def get_mcp_mount_path() -> str:
    return _normalize_mount_path(get_settings().mcp_mount_path)
