from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.conversation.service import apply_action, consume_stream, create_conversation, queue_message
from app.domains.quick_note.service import delete_note
from app.domains.schedule.service import delete_schedule
from app.models import Attachment, ConversationPendingState, NotificationAudit, QuickNote, ReminderJob, Schedule, User
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
        self.assertGreaterEqual(write_memory_mock.call_count, 1)

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

    @patch("app.domains.conversation.service.invoke_synora_tool", new_callable=AsyncMock)
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="教学例会")
    @patch.object(ModelAdapter, "aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_schedule_message_creates_pending_cards(self, _intent_mock, _title_mock, invoke_tool_mock) -> None:
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
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会"),
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
    async def test_confirm_schedule_action_returns_result_card(
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
                source_text="?????????????????",
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
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        _, assistant_messages = apply_action(
            self.db,
            self.user.id,
            thread.id,
            ConversationActionRequest(action="confirm_schedule_draft"),
        )

        self.assertEqual(assistant_messages[0].message_type, "result_card")
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNone(pending)
        self.assertGreaterEqual(write_memory_mock.call_count, 1)

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
            ConversationSendMessageRequest(text_content="记一下：下周整理论文实验记录"),
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
        self.assertEqual(confirm_messages[0].message_type, "result_card")
        self.assertGreaterEqual(write_memory_mock.call_count, 1)

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
                text_content="????????",
                attachment_ids=[attachment.id],
                selected_tool="schedule",
            ),
        )

        payload = dict(user_message.structured_payload_json or {})
        self.assertEqual(payload["selected_tool"], "schedule")
        self.assertEqual(len(payload["attachment_refs"]), 1)
        self.assertEqual(payload["attachment_refs"][0]["attachment_id"], attachment.id)
        self.assertEqual(payload["attachment_refs"][0]["file_name"], "agenda.pdf")

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


if __name__ == "__main__":
    unittest.main()

