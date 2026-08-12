"""Agent 工具集聚合：本服务进程内 MCP + 外部 MCP servers。

每个 server 单独 ``get_tools`` 并 try/except 降级——单个外部 server
配置错误或不可达时仅记日志，不阻断主流程。
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.runtime.mcp_client import get_mcp_client

logger = logging.getLogger(__name__)


async def build_agent_tools(exclude_names: set[str] | None = None):
    """聚合本服务进程内 MCP + 外部 MCP servers 的工具。

    ``exclude_names``：按工具名过滤（外部 MCP 工具同样受过滤），用于收窄
    general_chat 分支的工具集，避免其越权调用 intake 写工具产生无卡片、
    无 pending 的“伪草稿”。
    """
    settings = get_settings()
    server_names = ["synora", *(cfg.name for cfg in settings.mcp_servers)]
    tools = []
    for name in server_names:
        try:
            server_tools = await get_mcp_client().get_tools(server_name=name)
            if exclude_names:
                server_tools = [tool for tool in server_tools if tool.name not in exclude_names]
            tools.extend(server_tools)
        except Exception as exc:
            logger.warning("mcp_server_unavailable name=%s error=%s", name, exc)
    return tools
