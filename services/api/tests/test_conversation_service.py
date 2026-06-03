from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.conversation.service import apply_action, consume_stream, create_conversation, delete_conversation, queue_message, rewind_last_turn, update_conversation_title
from app.domains.quick_note.service import delete_note
from app.domains.schedule.service import delete_schedule
from app.models import ApprovalRequest, Attachment, ConversationMessage, ConversationPendingState, NotificationAudit, QuickNote, ReminderJob, Schedule, User
from app.runtime.model_adapter import ModelAdapter
from app.schemas.common import EventDateTimeValue
from app.schemas.conversation import ConversationActionRequest, ConversationSendMessageRequest
from app.schemas.schedule import ScheduleEventDraft


class _FakeGeneralChatAgent:
    async def astream_events(self, _payload, version="v2"):
        assert version == "v2"
        yield {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="好的，")}}
        yield {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="我来帮你一起整理。")}}
        yield {
            "event": "on_chain_end",
            "data": {"output": {"messages": [SimpleNamespace(content="好的，我来帮你一起整理。")]}},
        }


class _FakeToolOnlyGeneralChatAgent:
    async def astream_events(self, _payload, version="v2"):
        assert version == "v2"
        yield {
            "event": "on_chain_end",
            "data": {"output": {"messages": [object()]}},
        }


class ConversationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)
        Base.metadata.create_all(self.engine)
        self.db = self.session_factory()
        self.user = User(
            email="han.teacher@example.com",
            display_name="韩老师",
            password_hash="hashed-password",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _draft(self) -> ScheduleEventDraft:
        return ScheduleEventDraft(
            title="教学例会",
            location="学院会议室",
            details="讨论课程安排",
            source_text="明天下午三点开教学例会",
            isAllDay=False,
            start=EventDateTimeValue(dateTime=datetime.fromisoformat("2026-05-24T15:00:00+08:00"), timeZone="Asia/Shanghai"),
            end=EventDateTimeValue(dateTime=datetime.fromisoformat("2026-05-24T16:00:00+08:00"), timeZone="Asia/Shanghai"),
            recurrence=[],
            source_attachment_ids=[],
            parse_confidence=0.92,
            evidence_digest=["明天下午三点", "教学例会"],
        )

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.get_synora_tools", new_callable=AsyncMock, return_value=[])
    @patch.object(ModelAdapter, "build_general_chat_agent", return_value=_FakeGeneralChatAgent())
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="教学安排")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    async def test_send_general_chat_message(
        self,
        memory_mock,
        _intent_mock,
        _title_mock,
        _agent_mock,
        _tools_mock,
        write_memory_mock,
    ) -> None:
        memory_mock.return_value = SimpleNamespace(
            summary="韩老师通常希望提前一天提醒。",
            items=[{"title": "提醒偏好", "content": "通常提前一天提醒"}],
        )
        thread = create_conversation(self.db, self.user.id)
        _, user_message, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好，帮我看看今天安排"),
        )

        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(user_message.text_content, "你好，帮我看看今天安排")
        self.assertEqual(events[0]["event"], "run_started")
        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertEqual(self.db.get(type(thread), thread.id).title, "教学安排")
        self.assertEqual(self.db.get(type(assistant_message), assistant_message.id).text_content, "好的，我来帮你一起整理。")
        self.assertEqual(write_memory_mock.call_count, 0)

    async def test_stream_returns_run_failed_when_llm_not_configured(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好，帮我看看今天安排"),
        )

        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(events[0]["event"], "run_started")
        self.assertEqual(events[-1]["event"], "run_failed")
        self.assertEqual(events[-1]["data"]["code"], "llm_not_configured")
        self.assertFalse(events[-1]["data"]["retryable"])
        refreshed = self.db.get(type(assistant_message), assistant_message.id)
        self.assertEqual(refreshed.status, "failed")
        self.assertEqual(refreshed.text_content, "")

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.get_synora_tools", new_callable=AsyncMock, return_value=[])
    @patch.object(ModelAdapter, "build_general_chat_agent", return_value=_FakeToolOnlyGeneralChatAgent())
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="测试聊天")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    async def test_general_chat_does_not_leak_langchain_internal_repr(
        self,
        memory_mock,
        _intent_mock,
        _title_mock,
        _agent_mock,
        _tools_mock,
        _write_memory_mock,
    ) -> None:
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好"),
        )

        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertEqual(self.db.get(type(assistant_message), assistant_message.id).text_content, "")

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.invoke_synora_tool", new_callable=AsyncMock)
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="教学例会")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_schedule_message_creates_pending_cards(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        draft = self._draft()
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["明天下午三点"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)

        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        card_events = [item for item in events if item["event"] == "card_snapshot"]
        self.assertEqual([item["data"]["message"]["message_type"] for item in card_events], ["schedule_draft_card", "conflict_card"])
        self.assertTrue(any(item["event"] == "approval_required" for item in events))
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNotNone(pending)
        self.assertEqual(pending.stage, "approval_pending")

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.create_schedule_after_approval")
    @patch("app.domains.conversation.service.invoke_synora_tool", new_callable=AsyncMock)
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="教学例会")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_confirm_schedule_action_updates_existing_cards_in_place(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        create_after_mock,
        write_memory_mock,
    ) -> None:
        draft = self._draft()
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["明天下午三点"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash",
                    },
                },
            ),
        ]
        create_after_mock.return_value = (
            SimpleNamespace(
                id=10,
                title="教学例会",
                details="讨论课程安排",
                source_text="明天下午三点在学院会议室开教学例会",
                start_at=datetime.fromisoformat("2026-05-24T07:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-05-24T08:00:00+00:00"),
                time_zone="Asia/Shanghai",
            ),
            [SimpleNamespace(channel="email"), SimpleNamespace(channel="wecom_robot")],
        )
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        _, assistant_messages = apply_action(
            self.db,
            self.user.id,
            thread.id,
            ConversationActionRequest(action="confirm_schedule_draft"),
        )

        self.assertEqual(assistant_messages, [])
        cards = self.db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == thread.id, ConversationMessage.action_group_id.is_not(None))
        ).all()
        self.assertTrue(cards)
        self.assertTrue(all((item.structured_payload_json or {}).get("lifecycle_status") == "confirmed" for item in cards))
        self.assertTrue(all((item.structured_payload_json or {}).get("is_actionable") is False for item in cards))
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNone(pending)
        self.assertGreaterEqual(write_memory_mock.call_count, 1)

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.create_schedule_after_approval")
    @patch("app.domains.conversation.service.invoke_synora_tool", new_callable=AsyncMock)
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="教学例会")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_confirm_schedule_action_succeeds_when_card_finalize_fails(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        create_after_mock,
        write_memory_mock,
    ) -> None:
        draft = self._draft()
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["明天下午三点"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash",
                    },
                },
            ),
        ]
        create_after_mock.return_value = (
            SimpleNamespace(
                id=11,
                title="教学例会",
                details="讨论课程安排",
                source_text="明天下午三点在学院会议室开教学例会",
                start_at=datetime.fromisoformat("2026-05-24T07:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-05-24T08:00:00+00:00"),
                time_zone="Asia/Shanghai",
                reminder_preset="previous_day_1700",
            ),
            [SimpleNamespace(channel="email")],
        )
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        with patch("app.domains.conversation.service._mark_action_group_status", side_effect=RuntimeError("card finalize failed")):
            _, assistant_messages = apply_action(
                self.db,
                self.user.id,
                thread.id,
                ConversationActionRequest(action="confirm_schedule_draft"),
            )

        self.assertEqual(assistant_messages, [])
        self.assertGreaterEqual(write_memory_mock.call_count, 1)
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNotNone(pending)

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.save_note_after_approval")
    @patch("app.domains.conversation.service.invoke_synora_tool", new_callable=AsyncMock)
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="实验记录")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="quick_note_intake")
    async def test_quick_note_message_and_confirm(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        save_note_mock,
        write_memory_mock,
    ) -> None:
        invoke_tool_mock.return_value = (
            SimpleNamespace(content="note"),
            {
                "status": "pending_approval",
                "normalized_content": "下周整理论文实验记录",
                "preview_tags": ["科研", "待办"],
                "attachment_ids": [],
                "evidence_digest": ["论文", "实验记录"],
                "approval": {
                    "approval_token": "quick-note-token",
                    "action": "create_quick_note",
                    "expires_at": datetime.now(timezone.utc).isoformat(),
                    "draft_hash": "quick-note-hash",
                },
            },
        )
        save_note_mock.return_value = SimpleNamespace(
            id=7,
            content="下周整理论文实验记录",
            topic_tags_json=["科研", "待办"],
        )
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="记一下：下周整理论文实验记录", selected_tool="quick_note"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]
        card_events = [item for item in events if item["event"] == "card_snapshot"]

        self.assertEqual([item["data"]["message"]["message_type"] for item in card_events], ["quick_note_preview_card"])

        _, confirm_messages = apply_action(
            self.db,
            self.user.id,
            thread.id,
            ConversationActionRequest(action="confirm_quick_note"),
        )
        self.assertEqual(confirm_messages, [])
        cards = self.db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == thread.id, ConversationMessage.message_type == "quick_note_preview_card")
        ).all()
        self.assertTrue(cards)
        self.assertTrue(all((item.structured_payload_json or {}).get("lifecycle_status") == "confirmed" for item in cards))
        self.assertGreaterEqual(write_memory_mock.call_count, 1)

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.invoke_synora_tool", new_callable=AsyncMock)
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="实验记录")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="quick_note_intake")
    async def test_pending_quick_note_regenerates_new_revision(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="note-1"),
                {
                    "status": "pending_approval",
                    "normalized_content": "下周整理论文实验记录",
                    "preview_tags": ["科研", "待办"],
                    "attachment_ids": [],
                    "evidence_digest": ["论文", "实验记录"],
                    "approval": {
                        "approval_token": "quick-note-token-1",
                        "action": "create_quick_note",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "quick-note-hash-1",
                    },
                },
            ),
            (
                SimpleNamespace(content="note-2"),
                {
                    "status": "pending_approval",
                    "normalized_content": "下周三整理论文实验记录并补充图表",
                    "preview_tags": ["科研", "待办", "图表"],
                    "attachment_ids": [],
                    "evidence_digest": ["下周三", "图表"],
                    "approval": {
                        "approval_token": "quick-note-token-2",
                        "action": "create_quick_note",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "quick-note-hash-2",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, _, first_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="记一下：下周整理论文实验记录", selected_tool="quick_note"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, first_run.stream_token)]

        _, _, _, second_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="改成下周三，并补充图表"),
        )
        second_events = [item async for item in consume_stream(self.db, self.user.id, thread.id, second_run.stream_token)]

        cards = [item["data"]["message"] for item in second_events if item["event"] == "card_snapshot"]
        self.assertEqual([item["message_type"] for item in cards], ["quick_note_preview_card"])
        self.assertEqual(cards[0]["revision"], 2)
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertEqual(pending.pending_type, "quick_note")
        self.assertEqual(int(pending.meta_json.get("revision") or 0), 2)
        history = self.db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == thread.id)
            .order_by(ConversationMessage.id.asc())
        ).all()
        old_cards = [item for item in history if item.message_type == "quick_note_preview_card" and item.revision == 1]
        self.assertTrue(old_cards)
        self.assertTrue(all((item.structured_payload_json or {}).get("lifecycle_status") == "superseded" for item in old_cards))

    def test_delete_schedule_cascades_reminders_and_audits(self) -> None:
        schedule = Schedule(
            user_id=self.user.id,
            title="测试日程",
            location="会议室",
            details="测试",
            source_text="测试",
            start_at=datetime.now(timezone.utc),
            end_at=datetime.now(timezone.utc),
            time_zone="Asia/Shanghai",
            is_all_day=False,
            recurrence_rules_json=[],
            reminder_offsets_minutes_json=[-30],
            source_attachment_ids=[],
            parse_confidence=0.8,
            scheduled_at=datetime.now(timezone.utc),
            duration_minutes=60,
            reminder_at=datetime.now(timezone.utc),
            source_type="text",
        )
        self.db.add(schedule)
        self.db.commit()
        self.db.refresh(schedule)

        reminder = ReminderJob(
            schedule_id=schedule.id,
            channel="email",
            scheduled_for=datetime.now(timezone.utc),
            status="pending",
        )
        self.db.add(reminder)
        self.db.commit()
        self.db.refresh(reminder)

        audit = NotificationAudit(
            user_id=self.user.id,
            reminder_job_id=reminder.id,
            channel="email",
            recipient="han.teacher@example.com",
            subject="提醒",
            payload_json="{}",
            status="queued",
            provider="smtp",
        )
        self.db.add(audit)
        self.db.commit()

        delete_schedule(self.db, self.user.id, schedule.id)

        self.assertIsNone(self.db.get(Schedule, schedule.id))
        self.assertIsNone(self.db.get(ReminderJob, reminder.id))
        self.assertIsNone(self.db.get(NotificationAudit, audit.id))


    def test_queue_message_stores_user_message_metadata(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        attachment = Attachment(
            user_id=self.user.id,
            file_name="agenda.pdf",
            content_type="application/pdf",
            source_type="attachment",
            object_key="attachments/agenda.pdf",
            storage_bucket="synora",
            size_bytes=2048,
            status="uploaded",
        )
        self.db.add(attachment)
        self.db.commit()
        self.db.refresh(attachment)

        _, user_message, _, _ = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(
                text_content="请帮我处理这个附件",
                attachment_ids=[attachment.id],
                selected_tool="schedule",
            ),
        )

        payload = dict(user_message.structured_payload_json or {})
        self.assertEqual(payload["selected_tool"], "schedule")
        self.assertEqual(len(payload["attachment_refs"]), 1)
        self.assertEqual(payload["attachment_refs"][0]["attachment_id"], attachment.id)
        self.assertEqual(payload["attachment_refs"][0]["file_name"], "agenda.pdf")

    def test_update_conversation_title(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        updated = update_conversation_title(self.db, self.user.id, thread.id, "新的标题")
        self.assertEqual(updated.title, "新的标题")

    def test_delete_conversation_removes_runs_and_messages(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好"),
        )
        delete_conversation(self.db, self.user.id, thread.id)
        self.assertIsNone(self.db.get(type(thread), thread.id))
        self.assertIsNone(self.db.get(type(agent_run), agent_run.id))

    def test_delete_conversation_removes_pending_and_related_approvals(self) -> None:
        from app.domains.approval.service import create_approval_request

        thread = create_conversation(self.db, self.user.id)
        user_message = ConversationMessage(
            conversation_id=thread.id,
            role="user",
            message_type="text",
            status="sent",
            text_content="帮我记一下下周汇报",
            structured_payload_json={},
            action_group_id="group-1",
            revision=1,
        )
        self.db.add(user_message)
        approval, token = create_approval_request(
            self.db,
            user_id=self.user.id,
            action="create_quick_note",
            payload={"content": "下周汇报"},
            draft_hash="draft-quick-note",
            normalized_payload={"content": "下周汇报"},
            evidence_digest=[],
            approval_scope="conversation_quick_note:group-1",
        )
        self.db.add(
            ConversationPendingState(
                conversation_id=thread.id,
                user_id=self.user.id,
                pending_type="quick_note",
                stage="approval_pending",
                draft_hash="draft-quick-note",
                approval_token=token,
                source_type="mixed",
                attachment_ids_json=[],
                payload_json={"content": "下周汇报"},
                meta_json={"action_group_id": "group-1", "revision": 1},
            )
        )
        self.db.commit()

        delete_conversation(self.db, self.user.id, thread.id)

        self.assertIsNone(self.db.get(type(thread), thread.id))
        self.assertIsNone(
            self.db.scalar(
                select(ConversationPendingState).where(
                    ConversationPendingState.conversation_id == thread.id
                )
            )
        )
        self.assertIsNone(self.db.get(ApprovalRequest, approval.id))

    def test_rewind_last_turn_restores_user_payload(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        attachment = Attachment(
            user_id=self.user.id,
            file_name="agenda.pdf",
            content_type="application/pdf",
            source_type="attachment",
            object_key="attachments/agenda-rewind.pdf",
            storage_bucket="synora",
            size_bytes=2048,
            status="uploaded",
        )
        self.db.add(attachment)
        self.db.commit()
        self.db.refresh(attachment)

        _, user_message, assistant_message, _ = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(
                text_content="帮我处理这个附件",
                attachment_ids=[attachment.id],
                selected_tool="schedule",
            ),
        )
        restored_thread, restored_message = rewind_last_turn(self.db, self.user.id, thread.id)
        self.assertEqual(restored_thread.id, thread.id)
        self.assertEqual(restored_message.text_content, user_message.text_content)
        self.assertEqual((restored_message.structured_payload_json or {}).get("selected_tool"), "schedule")
        refs = list((restored_message.structured_payload_json or {}).get("attachment_refs") or [])
        self.assertEqual(len(refs), 1)
        self.assertIsNone(self.db.get(ConversationMessage, user_message.id))
        self.assertIsNone(self.db.get(ConversationMessage, assistant_message.id))

    def test_delete_quick_note_removes_note(self) -> None:
        note = QuickNote(
            user_id=self.user.id,
            content="测试速记",
            tags_csv="科研",
            source_text="测试速记",
            source_type="text",
            source_attachment_ids=[],
            topic_tags_json=["科研"],
        )
        self.db.add(note)
        self.db.commit()
        self.db.refresh(note)

        delete_note(self.db, self.user.id, note.id)

        self.assertIsNone(self.db.get(QuickNote, note.id))

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.invoke_synora_tool", new_callable=AsyncMock)
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="蓝桥杯安排")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_schedule_regeneration_keeps_user_history_in_source_text(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        original = self._draft().model_copy(
            update={
                "title": "蓝桥杯国赛",
                "details": "用户下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛，比赛时间是 9:00-13:00。",
                "source_text": "我下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛了，比赛时间是9:00-13:00",
            }
        )
        revised = original.model_copy(
            update={
                "details": "用户下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛，比赛时间是 9:00-13:00。",
                "source_text": "我下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛了，比赛时间是9:00-13:00\n\n不对，是下周",
            }
        )
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft-1"),
                {
                    "status": "ok",
                    "draft": original.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-1",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["比赛时间是 9:00-13:00"],
                    "parse_confidence": 0.91,
                },
            ),
            (
                SimpleNamespace(content="conflicts-1"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token-1",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash-1",
                    },
                },
            ),
            (
                SimpleNamespace(content="draft-2"),
                {
                    "status": "ok",
                    "draft": revised.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-2",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["最新更正为下周"],
                    "parse_confidence": 0.96,
                },
            ),
            (
                SimpleNamespace(content="conflicts-2"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token-2",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash-2",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, _, first_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(
                text_content="我下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛了，比赛时间是9:00-13:00",
                selected_tool="schedule",
            ),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, first_run.stream_token)]

        _, _, _, second_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="不对，是下周"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, second_run.stream_token)]

        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        latest_draft = ScheduleEventDraft.model_validate(pending.payload_json)
        self.assertEqual(
            latest_draft.source_text,
            "我下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛了，比赛时间是9:00-13:00\n\n不对，是下周",
        )
        self.assertEqual(
            latest_draft.details,
            "用户下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛，比赛时间是 9:00-13:00。",
        )

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.invoke_synora_tool", new_callable=AsyncMock)
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="教学例会")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_pending_schedule_regenerates_new_revision_with_cards(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        original = self._draft()
        revised = original.model_copy(
            update={
                "source_text": "改成下周二下午三点在学院会议室开教学例会",
                "start": EventDateTimeValue(
                    dateTime=datetime.fromisoformat("2026-05-26T15:00:00+08:00"),
                    timeZone="Asia/Shanghai",
                ),
                "end": EventDateTimeValue(
                    dateTime=datetime.fromisoformat("2026-05-26T16:00:00+08:00"),
                    timeZone="Asia/Shanghai",
                ),
            }
        )
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": original.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-1",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["明天下午三点"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts-1"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token-1",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash-1",
                    },
                },
            ),
            (
                SimpleNamespace(content="draft-2"),
                {
                    "status": "ok",
                    "draft": revised.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-2",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["下周二下午三点"],
                    "parse_confidence": 0.96,
                },
            ),
            (
                SimpleNamespace(content="conflicts-2"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token-2",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash-2",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, _, first_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, first_run.stream_token)]

        _, _, _, second_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="改成下周二下午三点"),
        )
        second_events = [item async for item in consume_stream(self.db, self.user.id, thread.id, second_run.stream_token)]

        self.assertFalse(any("当前还有一项待确认内容" in str(item) for item in second_events))
        cards = [item["data"]["message"] for item in second_events if item["event"] == "card_snapshot"]
        self.assertEqual([item["revision"] for item in cards], [2, 2])
        history = self.db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == thread.id)
            .order_by(ConversationMessage.id.asc())
        ).all()
        old_cards = [item for item in history if item.action_group_id == cards[0]["action_group_id"] and item.revision == 1]
        self.assertTrue(old_cards)
        self.assertTrue(all((item.structured_payload_json or {}).get("lifecycle_status") == "superseded" for item in old_cards))
        self.assertTrue(all((item.structured_payload_json or {}).get("is_actionable") is False for item in old_cards))
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertEqual(int(pending.meta_json.get("revision") or 0), 2)

    def test_superseded_approval_token_is_rejected(self) -> None:
        from app.domains.approval.service import create_approval_request, consume_approval_request

        approval1, token1 = create_approval_request(
            self.db,
            user_id=self.user.id,
            action="update_schedule",
            payload={"schedule_id": 1},
            draft_hash="draft-1",
            normalized_payload={"title": "旧预检"},
            evidence_digest=[],
            approval_scope="schedule:update:1",
        )
        approval2, token2 = create_approval_request(
            self.db,
            user_id=self.user.id,
            action="update_schedule",
            payload={"schedule_id": 1},
            draft_hash="draft-2",
            normalized_payload={"title": "新预检"},
            evidence_digest=[],
            approval_scope="schedule:update:1",
        )
        self.assertEqual(approval2.status, "pending")
        refreshed = self.db.get(type(approval1), approval1.id)
        self.assertEqual(refreshed.status, "superseded")
        with self.assertRaises(ValueError):
            consume_approval_request(
                self.db,
                user_id=self.user.id,
                action="update_schedule",
                approval_token=token1,
                draft_hash="draft-1",
            )
        consume_approval_request(
            self.db,
            user_id=self.user.id,
            action="update_schedule",
            approval_token=token2,
            draft_hash="draft-2",
        )

    @patch("app.domains.conversation.service.write_user_memory.delay")
    @patch("app.domains.conversation.service.invoke_synora_tool", new_callable=AsyncMock)
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="创建提醒")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_conversation_without_selected_tool_returns_tool_selection_reminder(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午我去剪头发"),
        )

        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(invoke_tool_mock.await_count, 0)
        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertIn("请先选择“日程”工具", self.db.get(type(assistant_message), assistant_message.id).text_content or "")
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNone(pending)


if __name__ == "__main__":
    unittest.main()
