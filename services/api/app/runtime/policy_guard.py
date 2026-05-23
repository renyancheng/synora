from __future__ import annotations


class PolicyGuard:
    allowed_tools: set[str] = {"schedule", "quick_note"}

    def validate_selected_tool(self, selected_tool: str | None) -> None:
        if selected_tool is not None and selected_tool not in self.allowed_tools:
            raise ValueError("不支持的工具选择。")

    def ensure_write_allowed(self, approval_token: str | None) -> None:
        if not approval_token:
            raise ValueError("写入操作必须先确认再执行。")
