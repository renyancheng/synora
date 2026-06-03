import unittest
from unittest.mock import patch

from app.runtime.errors import LLMServiceError
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
            "location": "信息楼202",
            "details": "讨论下周课程安排",
            "start_at": None,
            "end_at": None,
            "missing_fields": ["start_at"],
            "ambiguity_flags": ["time_ambiguous"],
            "parse_confidence": 0.9,
            "evidence_digest": ["明天下午3点", "信息楼202"],
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

    @patch("app.runtime.tool_impls.build_attachment_prompt_assets", return_value=[])
    @patch("app.runtime.tool_impls.MemoryService.retrieve_context")
    @patch("app.runtime.tool_impls.ModelAdapter.suggest_quick_note_tags")
    def test_quick_note_draft_does_not_read_long_term_memory(
        self,
        suggest_tags_mock,
        retrieve_context_mock,
        _attachment_mock,
    ) -> None:
        suggest_tags_mock.return_value = {
            "normalized_content": "我更喜欢深色主题，图标尽量简洁，设置页别放太多说明文字。",
            "preview_tags": ["主题偏好", "界面偏好"],
            "evidence_digest": ["深色主题", "图标简洁", "设置页少说明文字"],
        }

        result = record_quick_note(
            db=None,
            user_id=1,
            content="记一下：我更喜欢深色主题，图标尽量简洁，设置页别放太多说明文字。",
            tags=[],
            attachment_ids=[],
            context={},
        )

        retrieve_context_mock.assert_not_called()
        merged_text = suggest_tags_mock.call_args.kwargs["merged_text"]
        self.assertNotIn("长期记忆", merged_text)
        self.assertNotIn("记忆提示", merged_text)
        self.assertEqual(
            result["normalized_content"],
            "我更喜欢深色主题，图标尽量简洁，设置页别放太多说明文字。",
        )

    @patch("app.runtime.tool_impls.build_attachment_prompt_assets", return_value=[])
    @patch("app.runtime.tool_impls.ModelAdapter.suggest_quick_note_tags")
    def test_quick_note_regeneration_prompt_uses_structured_sections(
        self,
        suggest_tags_mock,
        _attachment_mock,
    ) -> None:
        suggest_tags_mock.return_value = {
            "normalized_content": "下周三整理论文实验记录并补充图表",
            "preview_tags": ["科研", "待办", "图表"],
            "evidence_digest": ["下周三", "图表"],
        }

        result = record_quick_note(
            db=None,
            user_id=1,
            content="改成下周三，并补充图表",
            tags=[],
            attachment_ids=[],
            context={
                "pending_regeneration": "quick_note",
                "previous_note_content": "下周整理论文实验记录",
                "latest_user_text": "改成下周三，并补充图表",
            },
        )

        merged_text = suggest_tags_mock.call_args.kwargs["merged_text"]
        self.assertIn("上一版待确认速记：\n下周整理论文实验记录", merged_text)
        self.assertIn("本轮补充或修正：\n改成下周三，并补充图表", merged_text)
        self.assertNotIn("你正在修改同一条待确认速记", merged_text)
        self.assertNotIn("上一版速记：", merged_text)
        self.assertEqual(result["preview_tags"], ["科研", "待办", "图表"])

    @patch("app.runtime.tool_impls.build_attachment_prompt_assets", return_value=[])
    @patch(
        "app.runtime.tool_impls.ModelAdapter.extract_schedule",
        side_effect=LLMServiceError(
            "llm_invalid_response",
            "智能服务返回异常，本轮未完成。",
            retryable=True,
        ),
    )
    def test_parse_schedule_raises_instead_of_silent_fallback(self, _extract_schedule_mock, _attachment_mock) -> None:
        with self.assertRaises(LLMServiceError):
            parse_schedule_draft(
                db=None,
                user_id=1,
                text_content="明天下午三点开会",
                attachment_ids=[],
                context={"client_timezone": "Asia/Shanghai"},
            )


if __name__ == "__main__":
    unittest.main()
