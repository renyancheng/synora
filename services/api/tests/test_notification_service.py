import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.notification.service import (
    collect_due_jobs,
    dispatch_notification_core,
    get_notification_status_core,
    queue_notification_audit,
)
from app.models import NotificationAudit, ReminderJob, Schedule, User


class NotificationServiceTests(unittest.TestCase):
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
            email="han.teacher@example.com",
            display_name="韩老师",
            password_hash="hashed-password",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.schedule = Schedule(
            user_id=self.user.id,
            title="教学例会",
            details="讨论课程安排",
            source_text="明天下午三点在学院会议室开教学例会",
            start_at=datetime(2026, 5, 24, 7, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 5, 24, 8, 0, tzinfo=timezone.utc),
            time_zone="Asia/Shanghai",
        )
        self.db.add(self.schedule)
        self.db.commit()
        self.db.refresh(self.schedule)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _create_job(self, channel: str = "system") -> ReminderJob:
        job = ReminderJob(
            schedule_id=self.schedule.id,
            channel=channel,
            scheduled_for=datetime.now(timezone.utc),
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    @patch("app.domains.notification.service.send_system_push", return_value=None)
    def test_dispatch_system_channel_marks_delivered_and_job_sent(self, push_mock) -> None:
        job = self._create_job()

        result = dispatch_notification_core(db=self.db, reminder_job_id=job.id)

        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["provider"], "system")
        audit = self.db.get(NotificationAudit, result["delivery_id"])
        self.assertEqual(audit.channel, "system")
        self.assertEqual(audit.status, "delivered")
        self.assertIsNotNone(audit.delivered_at)
        self.assertEqual(audit.external_id, f"system-{audit.id}")
        self.db.refresh(job)
        self.assertEqual(job.status, "sent")
        push_mock.assert_called_once()

    @patch("app.domains.notification.service.send_system_push", return_value="token 123…: Unregistered")
    def test_dispatch_records_push_error_but_keeps_delivered(self, _push_mock) -> None:
        # FCM 失败不应改变 system 审计状态：前端轮询仍是兜底通道。
        job = self._create_job()

        result = dispatch_notification_core(db=self.db, reminder_job_id=job.id)

        self.assertEqual(result["status"], "delivered")
        audit = self.db.get(NotificationAudit, result["delivery_id"])
        self.assertEqual(audit.error_message, "token 123…: Unregistered")
        self.assertEqual(audit.status, "delivered")
        self.db.refresh(job)
        self.assertEqual(job.status, "sent")

    @patch("app.domains.notification.service.send_system_push", return_value=None)
    def test_collect_due_jobs_finds_past_pending_job(self, _push_mock) -> None:
        job = self._create_job()
        job.scheduled_for = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.db.commit()

        jobs = collect_due_jobs(self.db)

        self.assertEqual([item.id for item in jobs], [job.id])

    @patch("app.domains.notification.service.send_system_push", return_value=None)
    def test_get_notification_status_after_dispatch(self, _push_mock) -> None:
        job = self._create_job()

        result = dispatch_notification_core(db=self.db, reminder_job_id=job.id)
        status = get_notification_status_core(db=self.db, delivery_id=result["delivery_id"])

        self.assertEqual(status["channel_status"], "delivered")
        self.assertEqual(status["retry_info"]["retry_count"], 0)

    @patch("app.domains.notification.fcm._ensure_firebase", return_value=False)
    def test_send_system_push_skips_when_firebase_disabled(self, _ensure_mock) -> None:
        from app.domains.notification.fcm import send_system_push

        error = send_system_push(
            self.db,
            user_id=self.user.id,
            title="t",
            body="b",
            audit_id=1,
        )
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
