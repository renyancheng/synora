import unittest
from unittest.mock import patch

from app.runtime.model_adapter import ModelAdapter


class RuntimeRoutingTests(unittest.TestCase):
    def test_fallback_routes_precise_schedule_to_schedule_workflow(self) -> None:
        workflow = ModelAdapter._fallback_route_workflow(
            {
                "source_type": "text",
                "text_content": "明天下午3点在信息楼302参加软件工程教研会。",
            }
        )
        self.assertEqual(workflow, "schedule_intake")

    def test_fallback_routes_general_todo_to_quick_note(self) -> None:
        workflow = ModelAdapter._fallback_route_workflow(
            {
                "source_type": "text",
                "text_content": "下周整理论文实验记录和科研周报。",
            }
        )
        self.assertEqual(workflow, "quick_note_intake")

    @patch.object(ModelAdapter, "_json_completion", return_value={"workflow": "schedule_intake"})
    def test_ai_schedule_route_is_overridden_by_quick_note_heuristic(self, _json_completion_mock) -> None:
        workflow = ModelAdapter().route_workflow(
            {
                "source_type": "text",
                "text_content": "下周整理论文实验记录和科研周报。",
                "context": {"client_timezone": "Asia/Shanghai"},
            }
        )
        self.assertEqual(workflow, "quick_note_intake")


if __name__ == "__main__":
    unittest.main()
