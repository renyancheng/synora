import unittest

from app.runtime.agent_runtime_stub import parse_schedule_draft, suggest_note_tags


class RuntimeParserTests(unittest.TestCase):
    def test_parse_relative_datetime(self) -> None:
        result = parse_schedule_draft("明天 14:30 在实验室 讨论项目进度")
        self.assertIsNotNone(result.draft.scheduled_at)
        self.assertEqual(result.draft.location, "实验室 讨论项目进度")
        self.assertEqual(result.missing_fields, [])

    def test_suggest_note_tags(self) -> None:
        tags = suggest_note_tags("准备论文投稿清单和实验结果整理", [])
        self.assertIn("科研", tags)


if __name__ == "__main__":
    unittest.main()
