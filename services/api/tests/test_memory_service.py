from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.memory.service import MemoryService
from app.models import MemoryProfile, MemoryRecord, User


class MemoryServiceTests(unittest.TestCase):
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

    @patch.object(MemoryService, "is_enabled", return_value=False)
    def test_upsert_memory_records_and_profile_summary(self, _enabled_mock) -> None:
        service = MemoryService()
        records = service.upsert_memory_records(
            self.db,
            user_id=self.user.id,
            source_kind="conversation_message",
            source_ref_id="1",
            entries=[
                {"memory_type": "preference", "title": "提醒偏好", "content": "我通常希望提前一天提醒"},
                {"memory_type": "constraint", "title": "时间限制", "content": "晚上十点后不要再安排会议"},
            ],
        )

        self.assertEqual(len(records), 2)
        profile = self.db.query(MemoryProfile).filter_by(user_id=self.user.id).one()
        self.assertIn("提醒偏好", profile.summary_text)
        self.assertIn("时间限制", profile.summary_text)

    @patch.object(MemoryService, "is_enabled", return_value=False)
    def test_delete_and_clear_memory(self, _enabled_mock) -> None:
        service = MemoryService()
        records = service.upsert_memory_records(
            self.db,
            user_id=self.user.id,
            source_kind="confirmed_schedule",
            source_ref_id="10",
            entries=[{"memory_type": "confirmed_schedule", "title": "日程", "content": "每周一下午三点项目周会"}],
        )
        service.delete_record(self.db, user_id=self.user.id, memory_id=records[0].id)
        self.assertEqual(self.db.query(MemoryRecord).count(), 0)

        service.upsert_memory_records(
            self.db,
            user_id=self.user.id,
            source_kind="confirmed_quick_note",
            source_ref_id="11",
            entries=[{"memory_type": "confirmed_quick_note", "title": "速记", "content": "整理论文实验记录"}],
        )
        service.clear_user_memory(self.db, user_id=self.user.id)
        self.assertEqual(self.db.query(MemoryRecord).count(), 0)
        profile = self.db.query(MemoryProfile).filter_by(user_id=self.user.id).one()
        self.assertEqual(profile.summary_text, "")

    @patch.object(MemoryService, "is_enabled", return_value=False)
    def test_retrieve_context_degrades_without_vector_store(self, _enabled_mock) -> None:
        self.db.add(
            MemoryProfile(
                user_id=self.user.id,
                summary_text="韩老师通常希望提前一天提醒，晚上不要安排会议。",
                updated_at=datetime.now(timezone.utc),
            )
        )
        self.db.commit()
        context = MemoryService().retrieve_context(self.db, user_id=self.user.id, query_text="帮我安排明天下午开会")
        self.assertEqual(context.summary, "韩老师通常希望提前一天提醒，晚上不要安排会议。")
        self.assertEqual(context.items, [])

    def test_extract_memory_facts_ignores_generic_chat(self) -> None:
        service = MemoryService()
        self.assertEqual(service.extract_memory_facts(text="你好，今天天气怎么样"), [])

    def test_extract_memory_facts_keeps_preference_and_constraint(self) -> None:
        service = MemoryService()
        preference = service.extract_memory_facts(text="我通常希望提前一天提醒我")
        constraint = service.extract_memory_facts(text="晚上十点后不要再安排会议")
        self.assertEqual(preference[0]["memory_type"], "preference")
        self.assertEqual(constraint[0]["memory_type"], "constraint")


if __name__ == "__main__":
    unittest.main()
