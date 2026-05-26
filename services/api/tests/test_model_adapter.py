from unittest.mock import patch
import unittest

from app.config import Settings
from app.runtime.model_adapter import ModelAdapter


class ModelAdapterTests(unittest.TestCase):
    @patch("app.runtime.model_adapter.ChatOpenAI")
    def test_qwen_model_disables_thinking_by_default(self, chat_openai_mock) -> None:
        settings = Settings(
            llm_api_key="test-key",
            llm_model="qwen3.6-flash",
            llm_enable_thinking=False,
        )

        ModelAdapter(settings)._create_chat_model(temperature=0.2, streaming=False, enable_thinking=False)

        _, kwargs = chat_openai_mock.call_args
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})

    @patch("app.runtime.model_adapter.ChatOpenAI")
    def test_qwen_structured_or_tool_calling_forces_thinking_off(self, chat_openai_mock) -> None:
        settings = Settings(
            llm_api_key="test-key",
            llm_model="qwen3.6-flash",
            llm_enable_thinking=True,
        )

        ModelAdapter(settings)._create_chat_model(temperature=0.2, streaming=True, enable_thinking=False)

        _, kwargs = chat_openai_mock.call_args
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})

    @patch("app.runtime.model_adapter.ChatOpenAI")
    def test_non_qwen_model_does_not_send_thinking_flag(self, chat_openai_mock) -> None:
        settings = Settings(
            llm_api_key="test-key",
            llm_model="gpt-4.1-mini",
            llm_enable_thinking=False,
        )

        ModelAdapter(settings)._create_chat_model(temperature=0.2, streaming=False)

        _, kwargs = chat_openai_mock.call_args
        self.assertIsNone(kwargs["extra_body"])

    def test_extract_message_text_ignores_unknown_object_repr(self) -> None:
        class _CommandLike:
            def __str__(self) -> str:
                return "Command(update={'messages': [AIMessage(tool_calls=[...])]} )"

        self.assertEqual(ModelAdapter._extract_message_text(_CommandLike()), "")


if __name__ == "__main__":
    unittest.main()
