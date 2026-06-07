import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.quick_note.service import list_note_tags, list_notes, normalize_tags, update_note
from app.models import QuickNote, User


class QuickNoteServiceTests(unittest.TestCase):
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

    def test_normalize_tags_deduplicates_and_trims(self) -> None:
        self.assertEqual(
            normalize_tags(["  项目 ", "项目", "", "待办", "待办  "]),
            ["项目", "待办"],
        )

    def test_list_notes_filters_by_user_and_tag(self) -> None:
        self.db.add_all(
            [
                QuickNote(
                    user_id=self.user.id,
                    content="准备答辩提纲",
                    tags_csv="科研,答辩",
                    source_text="准备答辩提纲",
                    source_type="text",
                    source_attachment_ids=[],
                    topic_tags_json=["科研", "答辩"],
                ),
                QuickNote(
                    user_id=self.user.id,
                    content="整理报销票据",
                    tags_csv="行政",
                    source_text="整理报销票据",
                    source_type="text",
                    source_attachment_ids=[],
                    topic_tags_json=["行政"],
                ),
                QuickNote(
                    user_id=self.other_user.id,
                    content="别的用户笔记",
                    tags_csv="科研",
                    source_text="别的用户笔记",
                    source_type="text",
                    source_attachment_ids=[],
                    topic_tags_json=["科研"],
                ),
            ]
        )
        self.db.commit()

        all_items = list_notes(self.db, self.user.id)
        filtered = list_notes(self.db, self.user.id, tag="科研")

        self.assertEqual(len(all_items), 2)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].content, "准备答辩提纲")

    def test_list_notes_supports_query_and_tag_fallback(self) -> None:
        self.db.add_all(
            [
                QuickNote(
                    user_id=self.user.id,
                    content="下周去医院复查血常规",
                    tags_csv="健康,提醒",
                    source_text="下周去医院复查血常规",
                    source_type="text",
                    source_attachment_ids=[],
                    topic_tags_json=["健康", "提醒"],
                ),
                QuickNote(
                    user_id=self.user.id,
                    content="给导师发实验周报",
                    tags_csv="科研",
                    source_text="给导师发实验周报",
                    source_type="text",
                    source_attachment_ids=[],
                    topic_tags_json=["科研"],
                ),
            ]
        )
        self.db.commit()

        items = list_notes(self.db, self.user.id, query="医院", tag="健康")

        self.assertEqual([item.content for item in items], ["下周去医院复查血常规"])

    def test_list_note_tags_aggregates_current_user_only(self) -> None:
        self.db.add_all(
            [
                QuickNote(
                    user_id=self.user.id,
                    content="记录一",
                    tags_csv="科研,待办",
                    source_text="记录一",
                    source_type="text",
                    source_attachment_ids=[],
                    topic_tags_json=["科研", "待办"],
                ),
                QuickNote(
                    user_id=self.user.id,
                    content="记录二",
                    tags_csv="科研",
                    source_text="记录二",
                    source_type="text",
                    source_attachment_ids=[],
                    topic_tags_json=["科研"],
                ),
                QuickNote(
                    user_id=self.other_user.id,
                    content="记录三",
                    tags_csv="外部",
                    source_text="记录三",
                    source_type="text",
                    source_attachment_ids=[],
                    topic_tags_json=["外部"],
                ),
            ]
        )
        self.db.commit()

        items = list_note_tags(self.db, self.user.id)

        self.assertEqual(items, [{"tag": "科研", "count": 2}, {"tag": "待办", "count": 1}])

    def test_update_note_normalizes_tags(self) -> None:
        note = QuickNote(
            user_id=self.user.id,
            content="原始内容",
            tags_csv="旧标签",
            source_text="原始内容",
            source_type="text",
            source_attachment_ids=[],
            topic_tags_json=["旧标签"],
        )
        self.db.add(note)
        self.db.commit()
        self.db.refresh(note)

        updated = update_note(
            self.db,
            self.user.id,
            note_id=note.id,
            content="更新后的内容",
            tags=[" 科研 ", "科研", "待办"],
        )

        self.assertEqual(updated.content, "更新后的内容")
        self.assertEqual(updated.topic_tags_json, ["科研", "待办"])
        self.assertEqual(updated.tags_csv, "科研,待办")


if __name__ == "__main__":
    unittest.main()
