import unittest
from datetime import datetime

from app.domains.quick_note.service import build_note_hash
from app.domains.schedule.service import build_draft_hash
from app.schemas.common import EventDateTimeValue
from app.schemas.schedule import ScheduleEventDraft


class ApprovalHashingTests(unittest.TestCase):
    def test_note_hash_is_stable(self) -> None:
        first = build_note_hash("整理课程大纲", ["教学"], [])
        second = build_note_hash("整理课程大纲", ["教学"], [])
        self.assertEqual(first, second)

    def test_schedule_hash_changes_when_title_changes(self) -> None:
        base = ScheduleEventDraft(
            title="课程答疑",
            location="办公室",
            details="课程答疑",
            source_text="课程答疑",
            isAllDay=False,
            start=EventDateTimeValue(dateTime=datetime.fromisoformat("2026-05-25T14:00:00+08:00"), timeZone="Asia/Shanghai"),
            end=EventDateTimeValue(dateTime=datetime.fromisoformat("2026-05-25T15:00:00+08:00"), timeZone="Asia/Shanghai"),
            recurrence=[],
            source_attachment_ids=[],
            parse_confidence=0.8,
            evidence_digest=["课程答疑"],
        )
        changed = base.model_copy(update={"title": "组会"})
        self.assertNotEqual(build_draft_hash(base), build_draft_hash(changed))


if __name__ == "__main__":
    unittest.main()
