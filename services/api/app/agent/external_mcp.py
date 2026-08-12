"""外部 MCP server 配置模型与适配器配置转换。

``McpServerSettings`` 是 ``SYNORA_MCP_SERVERS`` 环境变量的 JSON 元素模型，
``to_adapter_config()`` 输出 ``langchain_mcp_adapters`` 的 connection 字典
（与 ``StreamableHttpConnection`` / ``StdioConnection`` TypedDict 兼容），
供 ``get_mcp_client()`` 聚合进 MultiServerMCPClient。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field


class McpServerSettings(BaseModel):
    name: str
    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 30.0
    sse_read_timeout_seconds: float = 300.0

    def to_adapter_config(self) -> dict[str, Any]:
        if self.transport == "stdio":
            return {
                "transport": "stdio",
                "command": self.command or "python",
                "args": list(self.args),
                "env": dict(self.env),
                "cwd": self.cwd,
            }
        return {
            "transport": "streamable_http",
            "url": self.url or "",
            "headers": dict(self.headers) or None,
            "timeout": timedelta(seconds=self.timeout_seconds),
            "sse_read_timeout": timedelta(seconds=self.sse_read_timeout_seconds),
        }
