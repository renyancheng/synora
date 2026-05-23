from __future__ import annotations

from functools import lru_cache


@lru_cache
def get_runtime_executor():
    from app.runtime.executor import Executor
    from app.runtime.model_adapter import ModelAdapter
    from app.runtime.planner import Planner
    from app.runtime.tool_impls import parse_schedule_draft, record_quick_note
    from app.runtime.tool_registry import ToolRegistry

    model_adapter = ModelAdapter()
    planner = Planner(model_adapter)
    registry = ToolRegistry()
    registry.register("parse_schedule_draft", parse_schedule_draft)
    registry.register("record_quick_note", record_quick_note)

    def _detect_schedule_conflicts(**kwargs):
        from app.domains.schedule.service import detect_conflicts_core

        return detect_conflicts_core(**kwargs)

    def _create_schedule_after_approval(**kwargs):
        from app.domains.schedule.service import create_schedule_after_approval_core

        return create_schedule_after_approval_core(**kwargs)

    def _dispatch_notification(**kwargs):
        from app.domains.notification.service import dispatch_notification_core

        return dispatch_notification_core(**kwargs)

    def _get_notification_status(**kwargs):
        from app.domains.notification.service import get_notification_status_core

        return get_notification_status_core(**kwargs)

    registry.register("detect_schedule_conflicts", _detect_schedule_conflicts)
    registry.register("create_schedule_after_approval", _create_schedule_after_approval)
    registry.register("dispatch_notification", _dispatch_notification)
    registry.register("get_notification_status", _get_notification_status)
    return Executor(planner, registry)
