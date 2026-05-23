import unittest

from app.domains.quick_note.service import build_note_hash
from app.domains.schedule.service import build_draft_hash
from app.schemas.schedule import ScheduleDraft


class ApprovalHashingTests(unittest.TestCase):
    def test_note_hash_is_stable(self) -> None:
        first = build_note_hash("整理课程大纲", ["教学"])
        second = build_note_hash("整理课程大纲", ["教学"])
        self.assertEqual(first, second)

    def test_schedule_hash_changes_when_title_changes(self) -> None:
        base = ScheduleDraft(
            title="课程答疑",
            location="办公室",
            details="课程答疑",
            source_text="课程答疑",
            scheduled_at=None,
            duration_minutes=60,
            reminder_at=None,
        )
        changed = base.model_copy(update={"title": "组会"})
        self.assertNotEqual(build_draft_hash(base), build_draft_hash(changed))


if __name__ == "__main__":
    unittest.main()
