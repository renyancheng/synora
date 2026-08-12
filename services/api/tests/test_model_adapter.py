from unittest.mock import patch
import unittest

from app.agent import llm
from app.agent.llm import QuickNotePreparationResult, ScheduleExtractionResult
from app.config import Settings


class ModelAdapterTests(unittest.TestCase):
    @patch("app.agent.llm.ChatOpenAI")
    def test_qwen_model_disables_thinking_by_default(self, chat_openai_mock) -> None:
        settings = Settings(
            llm_api_key="test-key",
            llm_model="qwen3.6-flash",
            llm_enable_thinking=False,
        )

        llm.create_chat_model(settings, temperature=0.2, streaming=False, enable_thinking=False)

        _, kwargs = chat_openai_mock.call_args
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})

    @patch("app.agent.llm.ChatOpenAI")
    def test_qwen_structured_or_tool_calling_forces_thinking_off(self, chat_openai_mock) -> None:
        settings = Settings(
            llm_api_key="test-key",
            llm_model="qwen3.6-flash",
            llm_enable_thinking=True,
        )

        llm.create_chat_model(settings, temperature=0.2, streaming=True, enable_thinking=False)

        _, kwargs = chat_openai_mock.call_args
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})

    @patch("app.agent.llm.ChatOpenAI")
    def test_non_qwen_model_does_not_send_thinking_flag(self, chat_openai_mock) -> None:
        settings = Settings(
            llm_api_key="test-key",
            llm_model="gpt-4.1-mini",
            llm_enable_thinking=False,
        )

        llm.create_chat_model(settings, temperature=0.2, streaming=False)

        _, kwargs = chat_openai_mock.call_args
        self.assertIsNone(kwargs["extra_body"])

    def test_extract_message_text_ignores_unknown_object_repr(self) -> None:
        class _CommandLike:
            def __str__(self) -> str:
                return "Command(update={'messages': [AIMessage(tool_calls=[...])]} )"

        self.assertEqual(llm.extract_message_text(_CommandLike()), "")

    def test_current_time_prompt_contains_timezone_and_readable_time(self) -> None:
        settings = Settings(
            llm_api_key="test-key",
            default_timezone="Asia/Shanghai",
        )

        prompt = llm.current_time_prompt(settings)

        self.assertIn("当前时区：Asia/Shanghai", prompt)
        self.assertIn("当前时间：", prompt)
        self.assertIn("当前本地时间：", prompt)

    def test_schedule_extraction_result_coerces_string_list_fields(self) -> None:
        result = ScheduleExtractionResult.model_validate(
            {
                "title": "理发",
                "details": "明天下午去剪头发。",
                "isAllDay": False,
                "recurrence": "每周一次",
                "missing_fields": "",
                "ambiguity_flags": None,
                "evidence_digest": "\n用户原话“明天下午我去剪头发”\n",
            }
        )

        self.assertEqual(result.recurrence, ["每周一次"])
        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.ambiguity_flags, [])
        self.assertEqual(result.evidence_digest, ["用户原话“明天下午我去剪头发”"])

    def test_quick_note_preparation_result_coerces_string_list_fields(self) -> None:
        result = QuickNotePreparationResult.model_validate(
            {
                "normalized_content": "整理实验记录",
                "preview_tags": "科研\n待办",
                "evidence_digest": "实验记录",
            }
        )

        self.assertEqual(result.preview_tags, ["科研", "待办"])
        self.assertEqual(result.evidence_digest, ["实验记录"])


if __name__ == "__main__":
    unittest.main()
