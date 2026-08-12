"""Agent 工具集聚合：本服务进程内 MCP + 外部 MCP servers。

每个 server 单独 ``get_tools`` 并 try/except 降级——单个外部 server
配置错误或不可达时仅记日志，不阻断主流程。
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.runtime.mcp_client import get_mcp_client

logger = logging.getLogger(__name__)


async def build_agent_tools():
    settings = get_settings()
    server_names = ["synora", *(cfg.name for cfg in settings.mcp_servers)]
    tools = []
    for name in server_names:
        try:
            tools.extend(await get_mcp_client().get_tools(server_name=name))
        except Exception as exc:
            logger.warning("mcp_server_unavailable name=%s error=%s", name, exc)
    return tools
