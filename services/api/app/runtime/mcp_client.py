from __future__ import annotations

import json
from datetime import timedelta
from functools import lru_cache
from uuid import uuid4

import httpx
from langchain_core.messages import ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.mcp.server import get_mcp_server


def _internal_httpx_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=get_mcp_server().streamable_http_app()),
        base_url="http://mcp.local",
        headers=headers or {},
        timeout=timeout,
        auth=auth,
    )


@lru_cache
def get_mcp_client() -> MultiServerMCPClient:
    from app.config import get_settings

    servers: dict[str, dict] = {
        "synora": {
            "transport": "streamable_http",
            "url": "http://mcp.local",
            "timeout": timedelta(seconds=30),
            "sse_read_timeout": timedelta(minutes=5),
            "httpx_client_factory": _internal_httpx_client_factory,
        }
    }
    for cfg in get_settings().mcp_servers:
        servers[cfg.name] = cfg.to_adapter_config()
    return MultiServerMCPClient(servers)


async def get_synora_tools():
    return await get_mcp_client().get_tools(server_name="synora")


def _extract_text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text)
                    continue
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                chunks.append(text)
        return "\n".join(chunk for chunk in chunks if chunk).strip()
    return str(content or "").strip()


def _coerce_structured_content(message: ToolMessage) -> dict:
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict):
        structured = artifact.get("structuredContent") or artifact.get("structured_content")
        if isinstance(structured, dict):
            return structured
        if isinstance(artifact, dict) and any(key in artifact for key in ("status", "draft", "approval")):
            return artifact

    content_text = _extract_text_content(message.content)
    if content_text:
        try:
            parsed = json.loads(content_text)
        except json.JSONDecodeError:
            return {"status": "ok", "content": content_text}
        if isinstance(parsed, dict):
            return parsed
    return {}


async def invoke_synora_tool(tool_name: str, args: dict) -> tuple[ToolMessage, dict]:
    tools = await get_synora_tools()
    tool = next((item for item in tools if item.name == tool_name), None)
    if tool is None:
        raise ValueError(f"找不到 MCP 工具：{tool_name}")

    result = await tool.ainvoke(
        {
            "name": tool.name,
            "args": args,
            "id": f"{tool_name}-{uuid4().hex}",
            "type": "tool_call",
        }
    )
    if not isinstance(result, ToolMessage):
        text = _extract_text_content(result)
        return ToolMessage(content=text, tool_call_id=f"{tool_name}-fallback"), {}
    return result, _coerce_structured_content(result)
