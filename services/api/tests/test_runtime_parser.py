import unittest
from unittest.mock import patch

from app.runtime.tool_impls import parse_schedule_draft, record_quick_note


class RuntimeParserTests(unittest.TestCase):
    @patch("app.runtime.tool_impls.build_attachment_prompt_assets", return_value=[])
    @patch("app.runtime.tool_impls.ModelAdapter.extract_schedule")
    def test_parse_schedule_with_model_result(self, extract_schedule_mock, _attachment_mock) -> None:
        extract_schedule_mock.return_value = {
            "title": "教学例会",
            "location": "学院会议室",
            "details": "讨论课程安排",
            "start_at": "2026-05-25T14:30:00+08:00",
            "end_at": "2026-05-25T15:30:00+08:00",
            "missing_fields": [],
            "ambiguity_flags": [],
            "parse_confidence": 0.92,
            "evidence_digest": ["明天下午三点", "学院会议室", "讨论课程安排"],
            "recurrence": [],
        }
        result = parse_schedule_draft(
            db=None,
            user_id=1,
            text_content="明天下午三点在学院会议室讨论课程安排",
            attachment_ids=[],
            context={"client_timezone": "Asia/Shanghai"},
        )
        self.assertEqual(result["draft"]["title"], "教学例会")
        self.assertEqual(result["draft"]["start"]["timeZone"], "Asia/Shanghai")
        self.assertEqual(result["missing_fields"], [])
        self.assertAlmostEqual(result["parse_confidence"], 0.92)

    @patch("app.runtime.tool_impls.build_attachment_prompt_assets", return_value=[])
    @patch("app.runtime.tool_impls.ModelAdapter.extract_schedule")
    def test_parse_schedule_infers_relative_time_when_model_returns_null(self, extract_schedule_mock, _attachment_mock) -> None:
        extract_schedule_mock.return_value = {
            "title": "软件工程教研会",
            "location": "信息楼 202",
            "details": "讨论下周课程安排",
            "start_at": None,
            "end_at": None,
            "missing_fields": ["start_at"],
            "ambiguity_flags": ["time_ambiguous"],
            "parse_confidence": 0.9,
            "evidence_digest": ["明天下午3点", "信息楼 202"],
            "recurrence": [],
        }
        result = parse_schedule_draft(
            db=None,
            user_id=1,
            text_content="明天下午3点在信息楼202参加软件工程教研会，讨论下周课程安排。",
            attachment_ids=[],
            context={"client_timezone": "Asia/Shanghai"},
        )
        self.assertEqual(result["missing_fields"], [])
        self.assertIsNotNone(result["draft"]["start"]["dateTime"])
        self.assertIsNotNone(result["draft"]["end"]["dateTime"])

    @patch("app.runtime.tool_impls.build_attachment_prompt_assets", return_value=[])
    @patch("app.runtime.tool_impls.ModelAdapter.suggest_quick_note_tags")
    def test_quick_note_preview_tags(self, suggest_tags_mock, _attachment_mock) -> None:
        suggest_tags_mock.return_value = {
            "normalized_content": "整理论文实验图表并准备投稿清单",
            "preview_tags": ["科研", "论文", "待办"],
            "evidence_digest": ["论文", "实验图表", "投稿清单"],
        }
        result = record_quick_note(
            db=None,
            user_id=1,
            content="整理论文实验图表并准备投稿清单",
            tags=[],
            attachment_ids=[],
            context={},
        )
        self.assertEqual(result["preview_tags"], ["科研", "论文", "待办"])
        self.assertEqual(result["normalized_content"], "整理论文实验图表并准备投稿清单")


if __name__ == "__main__":
    unittest.main()
