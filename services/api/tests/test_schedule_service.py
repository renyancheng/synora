from datetime import datetime
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.schedule.service import (
    build_approval_draft_hash,
    build_draft_hash,
    confirm_schedule_edit,
    list_schedules,
    preview_schedule_edit,
)
from app.models import Schedule, User
from app.schemas.common import EventDateTimeValue
from app.schemas.schedule import ScheduleEventDraft


class ScheduleServiceTests(unittest.TestCase):
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
        self.other_user = User(
            email="user2@example.com",
            display_name="用户二",
            password_hash="hashed-password",
            is_active=True,
        )
        self.db.add_all([self.user, self.other_user])
        self.db.commit()
        self.db.refresh(self.user)
        self.db.refresh(self.other_user)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _draft(self, *, reminder_preset: str = "previous_day_1700") -> ScheduleEventDraft:
        return ScheduleEventDraft(
            title="蓝桥杯国赛",
            location="东北大学浑南校区",
            details="9:00-13:00 参加蓝桥杯国赛",
            source_text="下周六去东北大学浑南校区参加蓝桥杯国赛",
            isAllDay=False,
            start=EventDateTimeValue(dateTime=datetime.fromisoformat("2026-06-13T09:00:00+08:00"), timeZone="Asia/Shanghai"),
            end=EventDateTimeValue(dateTime=datetime.fromisoformat("2026-06-13T13:00:00+08:00"), timeZone="Asia/Shanghai"),
            recurrence=[],
            reminder_preset=reminder_preset,
            source_attachment_ids=[],
            parse_confidence=0.95,
            evidence_digest=["下周六", "蓝桥杯国赛", "东北大学浑南校区"],
        )

    def test_confirm_schedule_edit_accepts_non_default_reminder_preset(self) -> None:
        schedule = Schedule(
            user_id=self.user.id,
            title="原始日程",
            location="原始地点",
            details="原始详情",
            source_text="原始描述",
            start_at=datetime.fromisoformat("2026-06-13T01:00:00+00:00"),
            end_at=datetime.fromisoformat("2026-06-13T02:00:00+00:00"),
            time_zone="Asia/Shanghai",
            is_all_day=False,
            recurrence_rules_json=[],
            reminder_offsets_minutes_json=[-960],
            reminder_preset="previous_day_1700",
            source_attachment_ids=[],
            parse_confidence=0.5,
            scheduled_at=datetime.fromisoformat("2026-06-13T01:00:00+00:00"),
            duration_minutes=60,
            reminder_at=datetime.fromisoformat("2026-06-12T09:00:00+00:00"),
            source_type="mixed",
            status="scheduled",
        )
        self.db.add(schedule)
        self.db.commit()
        self.db.refresh(schedule)

        draft = self._draft(reminder_preset="2h_before")
        _, _, approval, token = preview_schedule_edit(
            self.db,
            self.user.id,
            schedule_id=schedule.id,
            draft=draft,
        )

        self.assertEqual(approval.draft_hash, build_approval_draft_hash(draft))
        self.assertNotEqual(build_draft_hash(draft), approval.draft_hash)

        saved_schedule, jobs = confirm_schedule_edit(
            self.db,
            self.user.id,
            schedule_id=schedule.id,
            approval_token=token,
            draft=draft,
        )

        self.assertEqual(saved_schedule.reminder_preset, "2h_before")
        self.assertEqual(saved_schedule.reminder_offsets_minutes_json, [-120])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].channel, "system")

    def test_list_schedules_supports_query_and_user_isolation(self) -> None:
        self.db.add_all(
            [
                Schedule(
                    user_id=self.user.id,
                    title="去医院复查",
                    location="市医院",
                    details="下午复查牙齿",
                    source_text="明天下午去医院复查",
                    start_at=datetime.fromisoformat("2026-06-10T06:00:00+00:00"),
                    end_at=datetime.fromisoformat("2026-06-10T07:00:00+00:00"),
                    time_zone="Asia/Shanghai",
                    is_all_day=False,
                    recurrence_rules_json=[],
                    reminder_offsets_minutes_json=[-30],
                    reminder_preset="30m_before",
                    source_attachment_ids=[],
                    parse_confidence=0.8,
                    scheduled_at=datetime.fromisoformat("2026-06-10T06:00:00+00:00"),
                    duration_minutes=60,
                    reminder_at=datetime.fromisoformat("2026-06-10T05:30:00+00:00"),
                    source_type="mixed",
                    status="scheduled",
                ),
                Schedule(
                    user_id=self.other_user.id,
                    title="别的用户复查",
                    location="外院",
                    details="不应被搜到",
                    source_text="别的用户日程",
                    start_at=datetime.fromisoformat("2026-06-10T08:00:00+00:00"),
                    end_at=datetime.fromisoformat("2026-06-10T09:00:00+00:00"),
                    time_zone="Asia/Shanghai",
                    is_all_day=False,
                    recurrence_rules_json=[],
                    reminder_offsets_minutes_json=[-30],
                    reminder_preset="30m_before",
                    source_attachment_ids=[],
                    parse_confidence=0.8,
                    scheduled_at=datetime.fromisoformat("2026-06-10T08:00:00+00:00"),
                    duration_minutes=60,
                    reminder_at=datetime.fromisoformat("2026-06-10T07:30:00+00:00"),
                    source_type="mixed",
                    status="scheduled",
                ),
            ]
        )
        self.db.commit()

        items = list_schedules(self.db, self.user.id, query="医院")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "去医院复查")


if __name__ == "__main__":
    unittest.main()
