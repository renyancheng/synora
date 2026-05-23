from __future__ import annotations

from app.runtime.model_adapter import ModelAdapter


class Planner:
    def __init__(self, model_adapter: ModelAdapter) -> None:
        self._model_adapter = model_adapter

    def decide_workflow(self, payload: dict) -> str:
        return self._model_adapter.route_workflow(payload)

    def build_plan(self, workflow: str, payload: dict) -> list[dict]:
        if workflow == "schedule_intake":
            return [{"tool_name": "parse_schedule_draft", "payload": payload}]
        if workflow == "quick_note_intake":
            return [{"tool_name": "record_quick_note", "payload": payload}]
        if workflow == "notification_dispatch":
            return [{"tool_name": "dispatch_notification", "payload": payload}]
        raise ValueError(f"未知工作流：{workflow}")
