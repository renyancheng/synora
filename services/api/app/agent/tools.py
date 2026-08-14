"""Agent 工具集聚合：原生工具 + 本服务进程内 MCP + 外部 MCP servers。

原生工具（get_current_time / web_search）常驻可用，不依赖 MCP 注册状态：
即使 MCP server 异常，general_chat 白名单仍能绑到这两个工具，杜绝“未知工具”
空转。每个 MCP server 单独 ``get_tools`` 并 try/except 降级——单个外部 server
配置错误或不可达时仅记日志，不阻断主流程；与原生工具同名的 MCP 工具被去重。
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from app.agent.web_search import web_search
from app.config import get_settings
from app.runtime.mcp_client import get_mcp_client

logger = logging.getLogger(__name__)


@tool("get_current_time")
def native_get_current_time() -> dict:
    """查看当前时间：返回本地时间、时区、星期几（无副作用，不依赖 MCP）。"""
    from app.mcp.tools import get_current_time_tool

    return get_current_time_tool().model_dump(mode="json")


# @tool 装饰器会把函数名替换为 StructuredTool 实例，这里直接保存工具对象。
NATIVE_TOOLS: dict[str, object] = {
    tool_instance.name: tool_instance
    for tool_instance in (native_get_current_time, web_search)
}


def build_native_tools() -> list:
    return list(NATIVE_TOOLS.values())


async def build_agent_tools(exclude_names: set[str] | None = None, include_names: set[str] | None = None):
    """聚合原生工具 + 本服务进程内 MCP + 外部 MCP servers 的工具。

    ``include_names``：白名单过滤，只返回工具名在此集合内的工具（优先于
    ``exclude_names``）。用于 general_chat 仅绑定只读工具（get_current_time /
    web_search），杜绝未知工具空转。
    ``exclude_names``：黑名单过滤（原生与外部 MCP 工具同样受过滤），用于收窄
    general_chat 分支的工具集，避免其越权调用 intake 写工具产生无卡片、
    无 pending 的“伪草稿”。
    """
    settings = get_settings()
    tools: list = []
    seen_names: set[str] = set()
    for name, native in NATIVE_TOOLS.items():
        if include_names is not None and name not in include_names:
            continue
        if exclude_names and name in exclude_names:
            continue
        tools.append(native)
        seen_names.add(name)

    server_names = ["synora", *(cfg.name for cfg in settings.mcp_servers)]
    for name in server_names:
        try:
            server_tools = await get_mcp_client().get_tools(server_name=name)
            if include_names:
                server_tools = [item for item in server_tools if item.name in include_names]
            elif exclude_names:
                server_tools = [item for item in server_tools if item.name not in exclude_names]
            for item in server_tools:
                if item.name in seen_names:
                    continue
                seen_names.add(item.name)
                tools.append(item)
        except Exception as exc:
            logger.warning("mcp_server_unavailable name=%s error=%s", name, exc)
    return tools
