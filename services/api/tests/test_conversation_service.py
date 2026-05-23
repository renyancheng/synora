from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.conversation.service import apply_action, create_conversation, send_message
from app.domains.quick_note.service import delete_note
from app.domains.schedule.service import delete_schedule
from app.models import ConversationPendingState, NotificationAudit, QuickNote, ReminderJob, Schedule, User
from app.runtime.model_adapter import ModelAdapter
from app.schemas.conversation import ConversationActionRequest, ConversationSendMessageRequest
from app.schemas.schedule import ConflictCheckResponse, ScheduleDraft


class ConversationServiceTests(unittest.TestCase):
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

    def _draft(self) -> ScheduleDraft:
        return ScheduleDraft(
            title="教学例会",
            location="学院会议室",
            details="讨论课程安排",
            source_text="明天下午三点开教学例会",
            scheduled_at=datetime(2026, 5, 24, 15, 0, tzinfo=timezone.utc),
            duration_minutes=60,
            reminder_at=datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc),
            source_type="text",
            source_attachment_ids=[],
            parse_confidence=0.92,
            evidence_digest=["明天下午三点", "教学例会"],
        )

    @patch.object(ModelAdapter, "generate_chat_reply", return_value="好的，我来帮你一起整理。")
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="教学安排")
    @patch.object(ModelAdapter, "route_conversation_intent", return_value="general_chat")
    def test_send_general_chat_message(self, _intent_mock, _title_mock, _reply_mock) -> None:
        thread = create_conversation(self.db, self.user.id)

        _, user_message, assistant_messages = send_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好，帮我看看今天安排。"),
        )

        self.assertEqual(user_message.text_content, "你好，帮我看看今天安排。")
        self.assertEqual(assistant_messages[0].text_content, "好的，我来帮你一起整理。")
        refreshed = self.db.get(type(thread), thread.id)
        self.assertEqual(refreshed.title, "教学安排")

    @patch("app.domains.conversation.service.detect_conflicts")
    @patch("app.domains.conversation.service.create_schedule_draft")
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="教学例会")
    @patch.object(ModelAdapter, "route_conversation_intent", return_value="schedule_intake")
    def test_schedule_message_creates_pending_cards(self, _intent_mock, _title_mock, create_draft_mock, detect_conflicts_mock) -> None:
        draft = self._draft()
        create_draft_mock.return_value = (draft, "draft-hash", [], [], ["明天下午三点"], 0.92)
        detect_conflicts_mock.return_value = ConflictCheckResponse(
            conflict_items=[],
            suggestions=[],
            risk_level="low",
            approval={
                "approval_token": "approval-token",
                "action": "create_schedule",
                "expires_at": datetime.now(timezone.utc),
                "draft_hash": "draft-hash",
            },
        )
        thread = create_conversation(self.db, self.user.id)

        _, _, assistant_messages = send_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会。"),
        )

        self.assertEqual([item.message_type for item in assistant_messages], ["text", "schedule_draft_card", "conflict_card"])
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNotNone(pending)
        self.assertEqual(pending.stage, "awaiting_confirmation")

    @patch("app.domains.conversation.service.create_schedule_after_approval")
    @patch("app.domains.conversation.service.detect_conflicts")
    @patch("app.domains.conversation.service.create_schedule_draft")
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="教学例会")
    @patch.object(ModelAdapter, "route_conversation_intent", return_value="schedule_intake")
    def test_confirm_schedule_action_returns_result_card(
        self,
        _intent_mock,
        _title_mock,
        create_draft_mock,
        detect_conflicts_mock,
        create_after_mock,
    ) -> None:
        draft = self._draft()
        create_draft_mock.return_value = (draft, "draft-hash", [], [], ["明天下午三点"], 0.92)
        detect_conflicts_mock.return_value = ConflictCheckResponse(
            conflict_items=[],
            suggestions=[],
            risk_level="low",
            approval={
                "approval_token": "approval-token",
                "action": "create_schedule",
                "expires_at": datetime.now(timezone.utc),
                "draft_hash": "draft-hash",
            },
        )
        create_after_mock.return_value = (
            SimpleNamespace(
                id=10,
                title="教学例会",
                scheduled_at=draft.scheduled_at,
                reminder_at=draft.reminder_at,
                location="学院会议室",
            ),
            [SimpleNamespace(channel="email"), SimpleNamespace(channel="wecom_robot")],
        )
        thread = create_conversation(self.db, self.user.id)
        send_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会。"),
        )

        _, assistant_messages = apply_action(
            self.db,
            self.user.id,
            thread.id,
            ConversationActionRequest(action="confirm_schedule_draft"),
        )

        self.assertEqual(assistant_messages[0].message_type, "result_card")
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNone(pending)

    @patch("app.domains.conversation.service.save_note_after_approval")
    @patch("app.domains.conversation.service.create_quick_note_draft")
    @patch.object(ModelAdapter, "generate_conversation_title", return_value="实验记录")
    @patch.object(ModelAdapter, "route_conversation_intent", return_value="quick_note_intake")
    def test_quick_note_message_and_confirm(
        self,
        _intent_mock,
        _title_mock,
        create_note_mock,
        save_note_mock,
    ) -> None:
        create_note_mock.return_value = (
            "下周整理论文实验记录",
            ["科研", "待办"],
            "quick-note-token",
            ["论文", "实验记录"],
            SimpleNamespace(draft_hash="quick-note-hash"),
        )
        save_note_mock.return_value = SimpleNamespace(
            id=7,
            content="下周整理论文实验记录",
            topic_tags_json=["科研", "待办"],
        )
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_messages = send_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="记一下：下周整理论文实验记录。"),
        )

        self.assertEqual([item.message_type for item in assistant_messages], ["text", "quick_note_preview_card"])

        _, confirm_messages = apply_action(
            self.db,
            self.user.id,
            thread.id,
            ConversationActionRequest(action="confirm_quick_note"),
        )
        self.assertEqual(confirm_messages[0].message_type, "result_card")

    def test_delete_schedule_cascades_reminders_and_audits(self) -> None:
        schedule = Schedule(
            user_id=self.user.id,
            title="测试日程",
            location="会议室",
            details="测试",
            source_text="测试",
            scheduled_at=datetime.now(timezone.utc),
            duration_minutes=60,
            reminder_at=datetime.now(timezone.utc),
            source_type="text",
            source_attachment_ids=[],
            parse_confidence=0.8,
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
