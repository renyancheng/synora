from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import AgentRun, AgentToolCallAudit
from app.runtime.planner import Planner
from app.runtime.tool_registry import ToolRegistry


class Executor:
    def __init__(self, planner: Planner, tool_registry: ToolRegistry) -> None:
        self._planner = planner
        self._tool_registry = tool_registry

    def execute_workflow(self, db: Session, *, user_id: int, workflow: str, payload: dict) -> dict[str, Any]:
        workflow_payload = {"user_id": user_id, **payload}
        agent_run = AgentRun(user_id=user_id, workflow=workflow, input_json=workflow_payload)
        db.add(agent_run)
        db.commit()
        db.refresh(agent_run)

        try:
            result: dict[str, Any] = {}
            for step in self._planner.build_plan(workflow, workflow_payload):
                tool_name = step["tool_name"]
                tool_payload = step["payload"]
                result = self.execute_tool(db, agent_run.id, tool_name=tool_name, payload=tool_payload)
            agent_run.status = "completed"
            agent_run.output_json = result
            agent_run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return result
        except Exception as exc:
            agent_run.status = "failed"
            agent_run.error_message = str(exc)
            agent_run.completed_at = datetime.now(timezone.utc)
            db.commit()
            raise

    def execute_tool(self, db: Session, agent_run_id: int | None, *, tool_name: str, payload: dict) -> dict[str, Any]:
        audit = AgentToolCallAudit(
            agent_run_id=agent_run_id or 0,
            tool_name=tool_name,
            request_json=payload,
            status="running",
        )
        if agent_run_id:
            db.add(audit)
            db.commit()
            db.refresh(audit)

        try:
            tool = self._tool_registry.get(tool_name)
            result = tool(db=db, **payload)
            if agent_run_id:
                audit.status = "ok"
                audit.response_json = result
                db.commit()
            return result
        except Exception as exc:
            if agent_run_id:
                audit.status = "failed"
                audit.error_message = str(exc)
                db.commit()
            raise
