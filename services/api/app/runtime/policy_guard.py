from __future__ import annotations

from app.schemas.common import SourceType


class PolicyGuard:
    allowed_source_types: set[SourceType] = {"text", "screenshot", "photo", "chat_record", "email"}

    def validate_source_type(self, source_type: str) -> None:
        if source_type not in self.allowed_source_types:
            raise ValueError("不支持的输入类型。")

    def ensure_write_allowed(self, approval_token: str | None) -> None:
        if not approval_token:
            raise ValueError("写入操作必须先确认再执行。")
