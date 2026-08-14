from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import httpx
from fastapi import FastAPI
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import Base
from app.mcp.server import create_mcp_endpoint_app, create_mcp_exact_route, create_mcp_http_app, get_mcp_mount_path, get_mcp_server
from app.schemas.common import EventDateTimeValue
from app.schemas.schedule import ScheduleEventDraft


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        os.environ["SYNORA_MCP_BEARER_TOKEN"] = "test-token"
        os.environ["SYNORA_MCP_MOUNT_PATH"] = "/mcp"

        get_settings.cache_clear()
        create_mcp_http_app.cache_clear()
        create_mcp_endpoint_app.cache_clear()
        get_mcp_server.cache_clear()

        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
        Base.metadata.create_all(self.engine)

        self.session_local_patcher = patch("app.mcp.tools.SessionLocal", self.session_factory)
        self.session_local_patcher.start()

        server = get_mcp_server()
        server.streamable_http_app()

        @asynccontextmanager
        async def lifespan(_: FastAPI):
            async with server.session_manager.run():
                yield

        self.app = FastAPI(lifespan=lifespan)
        self.app.router.routes.append(create_mcp_exact_route())
        self.app.mount(f"{get_mcp_mount_path()}/", create_mcp_http_app())

    def tearDown(self) -> None:
        self.session_local_patcher.stop()
        self.engine.dispose()
        create_mcp_http_app.cache_clear()
        create_mcp_endpoint_app.cache_clear()
        get_mcp_server.cache_clear()
        get_settings.cache_clear()

    def _draft(self) -> ScheduleEventDraft:
        return ScheduleEventDraft(
            title="项目周会",
            location="软件学院会议室",
            details="讨论本周教学安排",
            source_text="下周一下午三点在软件学院会议室开项目周会",
            isAllDay=False,
            start=EventDateTimeValue(
                dateTime=datetime.fromisoformat("2026-05-25T15:00:00+08:00"),
                timeZone="Asia/Shanghai",
            ),
            end=EventDateTimeValue(
                dateTime=datetime.fromisoformat("2026-05-25T16:00:00+08:00"),
                timeZone="Asia/Shanghai",
            ),
            recurrence=[],
            source_attachment_ids=[],
            parse_confidence=0.93,
            evidence_digest=["下周一下午三点", "软件学院会议室", "项目周会"],
        )

    async def _http_client(self, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app)
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",
            headers=headers or {},
        )

    async def test_requires_bearer_token(self) -> None:
        async with self.app.router.lifespan_context(self.app):
            async with await self._http_client() as client:
                response = await client.get("/mcp")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    async def test_rejects_invalid_bearer_token(self) -> None:
        async with self.app.router.lifespan_context(self.app):
            async with await self._http_client({"Authorization": "Bearer wrong-token"}) as client:
                response = await client.get("/mcp")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")

    async def test_tools_list_exposes_expected_tools(self) -> None:
        async with self.app.router.lifespan_context(self.app):
            async with await self._http_client({"Authorization": "Bearer test-token"}) as client:
                async with streamable_http_client("http://localhost/mcp", http_client=client) as streams:
                    read_stream, write_stream, _ = streams
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.list_tools()

        tool_names = [tool.name for tool in result.tools]
        self.assertEqual(
            tool_names,
            [
                "parse_schedule_draft",
                "detect_schedule_conflicts",
                "create_schedule_after_approval",
                "prepare_quick_note_draft",
                "create_quick_note_after_approval",
                "dispatch_notification",
                "get_notification_status",
                "get_current_time",
            ],
        )

    async def test_parse_schedule_draft_tool_call(self) -> None:
        with patch(
            "app.mcp.tools.create_schedule_draft",
            return_value=(self._draft(), "draft-hash", [], [], ["下周一下午三点"], 0.93),
        ):
            async with self.app.router.lifespan_context(self.app):
                async with await self._http_client({"Authorization": "Bearer test-token"}) as client:
                    async with streamable_http_client("http://localhost/mcp", http_client=client) as streams:
                        read_stream, write_stream, _ = streams
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            result = await session.call_tool(
                                "parse_schedule_draft",
                                {
                                    "text_content": "下周一下午三点在软件学院会议室开项目周会",
                                    "attachment_ids": [],
                                    "context": {"client_timezone": "Asia/Shanghai"},
                                },
                            )

        structured = result.structuredContent or {}
        self.assertFalse(result.isError)
        self.assertEqual(structured["status"], "ok")
        self.assertEqual(structured["draft_hash"], "draft-hash")
        self.assertEqual(structured["draft"]["title"], "项目周会")
        self.assertEqual(structured["draft"]["start"]["timeZone"], "Asia/Shanghai")

    async def test_prepare_quick_note_draft_returns_pending_approval(self) -> None:
        approval = SimpleNamespace(
            action="create_quick_note",
            expires_at=datetime(2026, 5, 25, 8, 0, tzinfo=timezone.utc),
            draft_hash="note-hash",
        )
        with patch(
            "app.mcp.tools.create_quick_note_draft",
            return_value=("整理论文实验记录", ["科研", "待办"], "approval-token", ["实验记录"], approval),
        ):
            async with self.app.router.lifespan_context(self.app):
                async with await self._http_client({"Authorization": "Bearer test-token"}) as client:
                    async with streamable_http_client("http://localhost/mcp", http_client=client) as streams:
                        read_stream, write_stream, _ = streams
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            result = await session.call_tool(
                                "prepare_quick_note_draft",
                                {
                                    "content": "记一下：整理论文实验记录",
                                    "tags": [],
                                    "attachment_ids": [],
                                    "context": {},
                                },
                            )

        structured = result.structuredContent or {}
        self.assertFalse(result.isError)
        self.assertEqual(structured["status"], "pending_approval")
        self.assertEqual(structured["normalized_content"], "整理论文实验记录")
        self.assertEqual(structured["preview_tags"], ["科研", "待办"])
        self.assertEqual(structured["approval"]["approval_token"], "approval-token")

    async def test_prepare_quick_note_draft_tool_uses_context_user_id(self) -> None:
        approval = SimpleNamespace(
            action="create_quick_note",
            expires_at=datetime(2026, 5, 25, 8, 0, tzinfo=timezone.utc),
            draft_hash="note-hash",
        )
        with patch(
            "app.mcp.tools.create_quick_note_draft",
            return_value=("整理论文实验记录", ["科研", "待办"], "approval-token", ["实验记录"], approval),
        ) as create_mock:
            async with self.app.router.lifespan_context(self.app):
                async with await self._http_client({"Authorization": "Bearer test-token"}) as client:
                    async with streamable_http_client("http://localhost/mcp", http_client=client) as streams:
                        read_stream, write_stream, _ = streams
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            result = await session.call_tool(
                                "prepare_quick_note_draft",
                                {
                                    "content": "记一下：整理论文实验记录",
                                    "tags": [],
                                    "attachment_ids": [],
                                    "context": {"user_id": "123", "approval_scope": "conversation_quick_note:test"},
                                },
                            )

        structured = result.structuredContent or {}
        self.assertFalse(result.isError)
        self.assertEqual(structured["status"], "pending_approval")
        _, user_id_arg, _ = create_mock.call_args.args
        self.assertEqual(user_id_arg, 123)

    async def test_create_schedule_after_approval_returns_structured_business_error(self) -> None:
        with patch(
            "app.mcp.tools.create_schedule_after_approval",
            side_effect=ValueError("审批令牌无效或已过期"),
        ):
            async with self.app.router.lifespan_context(self.app):
                async with await self._http_client({"Authorization": "Bearer test-token"}) as client:
                    async with streamable_http_client("http://localhost/mcp", http_client=client) as streams:
                        read_stream, write_stream, _ = streams
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            result = await session.call_tool(
                                "create_schedule_after_approval",
                                {
                                    "approval_token": "bad-token",
                                    "normalized_draft": self._draft().model_dump(mode="json", by_alias=True),
                                },
                            )

        structured = result.structuredContent or {}
        self.assertFalse(result.isError)
        self.assertEqual(structured["status"], "error")
        self.assertEqual(structured["error_code"], "business_error")
        self.assertIn("审批令牌", structured["message"])


if __name__ == "__main__":
    unittest.main()
