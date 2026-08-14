"""联网搜索工具（智谱 bigmodel 网络搜索 API）。

- API 文档：https://docs.bigmodel.cn/api-reference/工具-api/网络搜索
- 模型固定为 search_std；API Key 通过环境变量 SYNORA_ZHIPU_WEB_SEARCH_API_KEY 配置。
- 未配置 / 超时 / 非 200 时返回结构化错误文本，工具自身不抛异常，保证 agent 流程
  不因搜索不可用而中断；调用审计由 conversation 服务统一记录。
"""

from __future__ import annotations

from typing import Any

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config import get_settings


class WebSearchQuery(BaseModel):
    query: str = Field(default="", description="搜索关键词或需要核查的问题")


class WebSearchResult(BaseModel):
    status: str  # ok | unconfigured | error
    content: str
    references: list[dict[str, str]]


def _extract_search_content(payload: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """解析 v4/web_search 响应：正文来自 search_result 列表，引用提取标题与链接。"""
    text_parts: list[str] = []
    references: list[dict[str, str]] = []
    raw_results = payload.get("search_result")
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                text_parts.append(content.strip())
            reference: dict[str, str] = {}
            title = item.get("title")
            link = item.get("link") or item.get("url")
            if title:
                reference["title"] = str(title)
            if link:
                reference["link"] = str(link)
            if reference:
                references.append(reference)
    return "\n\n".join(text_parts), references


def _run_search(query: str) -> WebSearchResult:
    settings = get_settings()
    if not str(query or "").strip():
        return WebSearchResult(
            status="error",
            content="联网搜索未收到搜索关键词，请基于已有知识回答或让用户补充具体问题。",
            references=[],
        )
    api_key = settings.zhipu_web_search_api_key.strip()
    if not api_key:
        return WebSearchResult(
            status="unconfigured",
            content="联网搜索未配置（缺少 SYNORA_ZHIPU_WEB_SEARCH_API_KEY），请直接基于已有知识回答或告知用户搜索不可用。",
            references=[],
        )
    url = f"{settings.zhipu_web_search_base_url.rstrip('/')}/web_search"
    payload = {
        "search_query": query.strip(),
        "search_engine": settings.zhipu_web_search_model,
        "search_intent": False,
        "count": 10,
        "search_recency_filter": "noLimit",
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        return WebSearchResult(
            status="error",
            content=f"联网搜索请求失败（{type(exc).__name__}），请基于已有知识回答。",
            references=[],
        )
    if response.status_code != 200:
        return WebSearchResult(
            status="error",
            content=f"联网搜索返回错误（HTTP {response.status_code}），请基于已有知识回答。",
            references=[],
        )
    try:
        data = response.json()
    except ValueError:
        return WebSearchResult(
            status="error",
            content="联网搜索返回内容无法解析，请基于已有知识回答。",
            references=[],
        )
    if not isinstance(data, dict):
        return WebSearchResult(
            status="error",
            content="联网搜索返回格式异常，请基于已有知识回答。",
            references=[],
        )
    content, references = _extract_search_content(data)
    if not content:
        return WebSearchResult(
            status="error",
            content="联网搜索没有返回有效内容，请基于已有知识回答。",
            references=references,
        )
    return WebSearchResult(status="ok", content=content, references=references)


@tool("web_search", args_schema=WebSearchQuery)
def web_search(query: str) -> dict[str, Any]:
    """联网搜索：当需要最新信息、外部资料或核查不确定的事实时使用。"""
    return _run_search(query).model_dump(mode="json")
