from datetime import datetime, timedelta, timezone
from unittest import mock
import unittest

from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import Base
from app.domains.conversation.service import (
    _upsert_pending_state,
    create_conversation,
    mark_cross_day_intent,
)
from app.models import (
    ConversationMessage,
    ConversationPendingState,
    NotificationAudit,
    User,
)
from app.tasks.pending import (
    _nudge_allowed,
    collect_due_cross_day_intents,
    collect_stale_draft_timeouts,
    handle_cross_day_intent_core,
    handle_draft_timeout_core,
    scan_pending_draft_timeouts,
    scan_pending_intents,
)


class PendingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
        Base.metadata.create_all(self.engine)
        self.db = self.session_factory()
        self.user = User(
            email="user1@example.com",
            display_name="用户一",
            password_hash="hashed-password",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _make_pending(
        self,
        *,
        stage: str = "needs_input",
        updated_at: datetime | None = None,
        intent_type: str | None = None,
        planned_at: datetime | None = None,
        meta_json: dict | None = None,
    ) -> ConversationPendingState:
        thread = create_conversation(self.db, self.user.id)
        pending = _upsert_pending_state(
            self.db,
            thread.id,
            self.user.id,
            pending_type="schedule_intake",
            stage=stage,
            draft_hash="abc",
            approval_token=None,
            attachment_ids=[],
            payload_json={"title": "蓝桥杯国赛", "start_at": "2026-08-20 09:00"},
            meta_json=meta_json or {},
            planned_at=planned_at,
            intent_type=intent_type,
        )
        if updated_at is not None:
            # 用 Core update 绕过 ORM onupdate，避免 updated_at 被 utc_now 覆盖。
            self.db.execute(
                update(ConversationPendingState)
                .where(ConversationPendingState.id == pending.id)
                .values(updated_at=updated_at)
            )
            self.db.commit()
            self.db.refresh(pending)
        return pending

    def _count_audits(self) -> int:
        return len(self.db.query(NotificationAudit).filter_by(user_id=self.user.id).all())

    # ---------- _nudge_allowed ----------

    def test_nudge_allowed_first_time(self) -> None:
        self.assertEqual(_nudge_allowed({}), (True, 1))

    def test_nudge_allowed_respects_max(self) -> None:
        self.assertEqual(_nudge_allowed({"nudge_count": 2}), (False, 2))
        self.assertEqual(_nudge_allowed({"nudge_count": 5}), (False, 5))

    def test_nudge_allowed_respects_cooldown(self) -> None:
        meta = {"nudge_count": 1, "last_nudge_at": datetime.now(timezone.utc).isoformat()}
        self.assertEqual(_nudge_allowed(meta), (False, 1))

    def test_nudge_allowed_after_cooldown(self) -> None:
        meta = {
            "nudge_count": 1,
            "last_nudge_at": (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(),
        }
        self.assertEqual(_nudge_allowed(meta), (True, 2))

    # ---------- handle_draft_timeout_core ----------

    @mock.patch("app.agent.llm.invoke_text", return_value="要现在确认「蓝桥杯国赛」吗？")
    def test_handle_draft_timeout_writes_message_and_notification(self, _invoke: mock.MagicMock) -> None:
        pending = self._make_pending(updated_at=datetime.now(timezone.utc) - timedelta(hours=8))

        result = handle_draft_timeout_core(self.db, pending.id)

        self.assertEqual(result, "ok")
        message = self.db.query(ConversationMessage).filter_by(
            conversation_id=pending.conversation_id, role="assistant", message_type="text"
        ).one()
        self.assertEqual(message.text_content, "要现在确认「蓝桥杯国赛」吗？")
        self.assertEqual(message.structured_payload_json.get("source"), "pending_nudge")

        audit = self.db.query(NotificationAudit).filter_by(user_id=self.user.id).one()
        self.assertEqual(audit.channel, "system")
        self.assertEqual(audit.status, "delivered")
        self.assertIn("蓝桥杯国赛", audit.payload_json)

        refreshed = self.db.get(ConversationPendingState, pending.id)
        self.assertEqual(refreshed.meta_json.get("nudge_count"), 1)
        self.assertIn("last_nudge_at", refreshed.meta_json)

    @mock.patch("app.agent.llm.invoke_text", return_value="")
    def test_handle_draft_timeout_falls_back_when_llm_empty(self, _invoke: mock.MagicMock) -> None:
        pending = self._make_pending(updated_at=datetime.now(timezone.utc) - timedelta(hours=8))

        result = handle_draft_timeout_core(self.db, pending.id)

        self.assertEqual(result, "ok")
        message = self.db.query(ConversationMessage).filter_by(
            conversation_id=pending.conversation_id, role="assistant", message_type="text"
        ).one()
        self.assertIn("蓝桥杯国赛", message.text_content)

    @mock.patch("app.agent.llm.invoke_text", return_value="追问")
    def test_handle_draft_timeout_snoozes_within_cooldown(self, _invoke: mock.MagicMock) -> None:
        pending = self._make_pending(
            updated_at=datetime.now(timezone.utc) - timedelta(hours=8),
            meta_json={"nudge_count": 1, "last_nudge_at": datetime.now(timezone.utc).isoformat()},
        )

        result = handle_draft_timeout_core(self.db, pending.id)

        self.assertEqual(result, "snoozed")
        self.assertEqual(self._count_audits(), 0)

    @mock.patch("app.agent.llm.invoke_text", return_value="追问")
    def test_handle_draft_timeout_returns_missing_for_unknown(self, _invoke: mock.MagicMock) -> None:
        self.assertEqual(handle_draft_timeout_core(self.db, 99999), "missing")

    # ---------- handle_cross_day_intent_core ----------

    @mock.patch("app.agent.llm.invoke_text", return_value="昨天的「蓝桥杯国赛」要现在安排吗？")
    def test_handle_cross_day_intent_triggers_once(self, _invoke: mock.MagicMock) -> None:
        pending = self._make_pending(
            stage="needs_input",
            intent_type="cross_day",
            planned_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        result = handle_cross_day_intent_core(self.db, pending.id)
        self.assertEqual(result, "ok")

        message = self.db.query(ConversationMessage).filter_by(
            conversation_id=pending.conversation_id, role="assistant", message_type="text"
        ).one()
        self.assertEqual(message.structured_payload_json.get("source"), "cross_day_followup")
        self.assertEqual(self._count_audits(), 1)

        refreshed = self.db.get(ConversationPendingState, pending.id)
        self.assertTrue(refreshed.meta_json.get("intent_triggered") is True)

        # 只触发一次
        result2 = handle_cross_day_intent_core(self.db, pending.id)
        self.assertEqual(result2, "already-triggered")
        self.assertEqual(self._count_audits(), 1)

    # ---------- collect 过滤 ----------

    def test_collect_stale_draft_timeouts_only_picks_stale(self) -> None:
        stale = self._make_pending(updated_at=datetime.now(timezone.utc) - timedelta(hours=10))
        self._make_pending(updated_at=datetime.now(timezone.utc) - timedelta(minutes=5))

        rows = collect_stale_draft_timeouts(self.db)

        self.assertEqual([row.id for row in rows], [stale.id])

    def test_collect_stale_draft_timeouts_ignores_completed(self) -> None:
        self._make_pending(stage="completed", updated_at=datetime.now(timezone.utc) - timedelta(hours=10))

        self.assertEqual(collect_stale_draft_timeouts(self.db), [])

    def test_collect_due_cross_day_intents_only_picks_due_untouched(self) -> None:
        due = self._make_pending(
            stage="needs_input",
            intent_type="cross_day",
            planned_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        # 未到唤醒时间
        self._make_pending(
            stage="needs_input",
            intent_type="cross_day",
            planned_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        # 非 cross_day
        self._make_pending(stage="needs_input", planned_at=datetime.now(timezone.utc) - timedelta(minutes=1))

        rows = collect_due_cross_day_intents(self.db)

        self.assertEqual([row.id for row in rows], [due.id])

    # ---------- scan 分发 ----------

    @mock.patch("app.tasks.pending.handle_draft_timeout.delay")
    def test_scan_draft_timeouts_dispatches(self, delay: mock.MagicMock) -> None:
        stale = self._make_pending(updated_at=datetime.now(timezone.utc) - timedelta(hours=10))

        with mock.patch("app.tasks.pending.collect_stale_draft_timeouts", return_value=[stale]):
            count = scan_pending_draft_timeouts()

        self.assertEqual(count, 1)
        delay.assert_called_once_with(stale.id)

    @mock.patch("app.tasks.pending.handle_cross_day_intent.delay")
    def test_scan_intents_dispatches_and_skips_triggered(self, delay: mock.MagicMock) -> None:
        due = self._make_pending(
            stage="needs_input",
            intent_type="cross_day",
            planned_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        done = self._make_pending(
            stage="needs_input",
            intent_type="cross_day",
            planned_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            meta_json={"intent_triggered": True},
        )

        with mock.patch("app.tasks.pending.collect_due_cross_day_intents", return_value=[due, done]):
            count = scan_pending_intents()

        self.assertEqual(count, 1)
        delay.assert_called_once_with(due.id)

    # ---------- mark_cross_day_intent ----------

    def test_mark_cross_day_intent_sets_fields(self) -> None:
        pending = self._make_pending()
        planned = datetime.now(timezone.utc) + timedelta(hours=24)

        updated = mark_cross_day_intent(self.db, pending.conversation_id, planned_at=planned)

        self.assertIsNotNone(updated)
        self.assertEqual(updated.intent_type, "cross_day")
        self.assertIsNotNone(updated.planned_at)

    def test_mark_cross_day_intent_no_pending_returns_none(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        self.assertIsNone(
            mark_cross_day_intent(self.db, thread.id, planned_at=datetime.now(timezone.utc))
        )


if __name__ == "__main__":
    unittest.main()
