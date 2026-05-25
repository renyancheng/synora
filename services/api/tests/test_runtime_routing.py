import unittest
from unittest.mock import patch

from app.runtime.errors import LLMServiceError
from app.runtime.model_adapter import ModelAdapter, WorkflowSelection


class RuntimeRoutingTests(unittest.TestCase):
    def test_fallback_routes_precise_schedule_to_schedule_workflow(self) -> None:
        workflow = ModelAdapter._fallback_route_workflow(
            {
                "text_content": "明天下午3点在信息楼302参加软件工程教研会。",
            }
        )
        self.assertEqual(workflow, "schedule_intake")

    def test_fallback_routes_general_todo_to_quick_note(self) -> None:
        workflow = ModelAdapter._fallback_route_workflow(
            {
                "text_content": "下周整理论文实验记录和科研周报。",
            }
        )
        self.assertEqual(workflow, "quick_note_intake")

    def test_route_workflow_raises_when_llm_not_configured(self) -> None:
        with self.assertRaises(LLMServiceError) as ctx:
            ModelAdapter().route_workflow(
                {
                    "text_content": "下周整理论文实验记录和科研周报。",
                    "context": {"client_timezone": "Asia/Shanghai"},
                }
            )
        self.assertEqual(ctx.exception.code, "llm_not_configured")

    @patch.object(ModelAdapter, "_invoke_structured", return_value=WorkflowSelection(workflow="schedule_intake"))
    def test_route_workflow_uses_model_result_when_llm_available(self, _invoke_mock) -> None:
        workflow = ModelAdapter().route_workflow(
            {
                "text_content": "下周整理论文实验记录和科研周报。",
                "context": {"client_timezone": "Asia/Shanghai"},
            }
        )
        self.assertEqual(workflow, "schedule_intake")


if __name__ == "__main__":
    unittest.main()
