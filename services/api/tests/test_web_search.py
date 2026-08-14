"""联网搜索工具与原生工具池测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agent.tools import build_agent_tools
from app.agent.web_search import _run_search, web_search
from app.config import get_settings
from app.runtime.mcp_client import get_mcp_client


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def post(self, url, *, headers=None, json=None):
        _FakeHttpClient.last_call = (url, headers or {}, json or {})
        return _FakeHttpClient.last_response

    last_call = None
    last_response: _FakeResponse | None = None


class _FakeMcpTool:
    def __init__(self, name: str) -> None:
        self.name = name


class WebSearchToolTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        _FakeHttpClient.last_call = None
        _FakeHttpClient.last_response = None

    def test_unconfigured_returns_structured_hint(self) -> None:
        with patch.object(get_settings(), "zhipu_web_search_api_key", ""):
            result = _run_search("今天天气")

        self.assertEqual(result.status, "unconfigured")
        self.assertIn("SYNORA_ZHIPU_WEB_SEARCH_API_KEY", result.content)
        # 工具调用本身不抛异常
        self.assertIn("unconfigured", web_search.invoke({"query": "今天天气"})["status"])

    def test_search_success_parses_content_and_references(self) -> None:
        _FakeHttpClient.last_response = _FakeResponse(
            200,
            {
                "created": 1786691538,
                "id": "req-1",
                "search_intent": [],
                "search_result": [
                    {
                        "content": "DeepSeek API 输入 1 元/百万 tokens。",
                        "title": "DeepSeek 定价",
                        "link": "https://example.com/pricing",
                        "refer": "ref_1",
                        "publish_date": "2026-08-06",
                    }
                ],
            },
        )
        with (
            patch.object(get_settings(), "zhipu_web_search_api_key", "test-key"),
            patch("app.agent.web_search.httpx.Client", _FakeHttpClient),
        ):
            result = _run_search("今天天气")

        self.assertEqual(result.status, "ok")
        self.assertIn("DeepSeek API 输入 1 元", result.content)
        self.assertEqual(result.references[0]["title"], "DeepSeek 定价")
        url, headers, body = _FakeHttpClient.last_call
        self.assertEqual(url, "https://open.bigmodel.cn/api/paas/v4/web_search")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(body["search_query"], "今天天气")
        self.assertEqual(body["search_engine"], "search_std")
        self.assertEqual(body["search_recency_filter"], "noLimit")
        self.assertFalse(body["search_intent"])

    def test_search_http_error_returns_structured_hint(self) -> None:
        _FakeHttpClient.last_response = _FakeResponse(401, {})
        with (
            patch.object(get_settings(), "zhipu_web_search_api_key", "test-key"),
            patch("app.agent.web_search.httpx.Client", _FakeHttpClient),
        ):
            result = _run_search("今天天气")

        self.assertEqual(result.status, "error")
        self.assertIn("HTTP 401", result.content)

    async def test_build_agent_tools_includes_native_tools_and_dedupes_mcp(self) -> None:
        async def fake_get_tools(server_name=None):
            return [_FakeMcpTool("get_current_time"), _FakeMcpTool("custom_mcp_tool")]

        with patch.object(get_mcp_client(), "get_tools", new=fake_get_tools):
            tools = await build_agent_tools(include_names={"get_current_time", "web_search"})

        self.assertEqual([item.name for item in tools], ["get_current_time", "web_search"])

        with patch.object(get_mcp_client(), "get_tools", new=fake_get_tools):
            tools = await build_agent_tools()
        names = [item.name for item in tools]
        self.assertEqual(names.count("get_current_time"), 1)
        self.assertIn("web_search", names)
        self.assertIn("custom_mcp_tool", names)

    async def test_build_agent_tools_excludes_native_tools(self) -> None:
        async def fake_get_tools(server_name=None):
            return []

        with patch.object(get_mcp_client(), "get_tools", new=fake_get_tools):
            tools = await build_agent_tools(exclude_names={"web_search"})

        names = [item.name for item in tools]
        self.assertIn("get_current_time", names)
        self.assertNotIn("web_search", names)


if __name__ == "__main__":
    unittest.main()
