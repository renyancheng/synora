"""联网搜索工具与原生工具池测试。"""

from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch

import app.agent.web_search as web_search_module
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
        _FakeHttpClient.call_count += 1
        _FakeHttpClient.last_call = (url, headers or {}, json or {})
        return _FakeHttpClient.last_response

    last_call = None
    last_response: _FakeResponse | None = None
    call_count = 0


class _FakeMcpTool:
    def __init__(self, name: str) -> None:
        self.name = name


class WebSearchToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _FakeHttpClient.last_call = None
        _FakeHttpClient.last_response = None
        _FakeHttpClient.call_count = 0
        web_search_module._SEARCH_CACHE.clear()

    def tearDown(self) -> None:
        _FakeHttpClient.last_call = None
        _FakeHttpClient.last_response = None
        _FakeHttpClient.call_count = 0
        web_search_module._SEARCH_CACHE.clear()

    def test_unconfigured_returns_structured_hint(self) -> None:
        with patch.object(get_settings(), "zhipu_web_search_api_key", ""):
            result = _run_search("今天天气")
            # 工具调用本身不抛异常
            self.assertIn("unconfigured", web_search.invoke({"query": "今天天气"})["status"])

        self.assertEqual(result.status, "unconfigured")
        self.assertIn("SYNORA_ZHIPU_WEB_SEARCH_API_KEY", result.content)

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

    def test_cache_returns_same_result_without_second_http_request(self) -> None:
        _FakeHttpClient.last_response = _FakeResponse(
            200,
            {
                "created": 1786691538,
                "id": "req-cache-1",
                "search_intent": [],
                "search_result": [
                    {
                        "content": "缓存命中测试内容。",
                        "title": "缓存命中",
                        "link": "https://example.com/cache",
                    }
                ],
            },
        )
        with (
            patch.object(get_settings(), "zhipu_web_search_api_key", "test-key"),
            patch("app.agent.web_search.httpx.Client", _FakeHttpClient),
        ):
            first = web_search.invoke({"query": "缓存命中"})
            second = web_search.invoke({"query": "缓存命中"})

        self.assertEqual(_FakeHttpClient.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "ok")
        self.assertIn("缓存命中测试内容", first["content"])

    def test_cache_key_normalizes_whitespace_and_case(self) -> None:
        _FakeHttpClient.last_response = _FakeResponse(
            200,
            {
                "created": 1786691538,
                "id": "req-cache-2",
                "search_intent": [],
                "search_result": [
                    {
                        "content": "归一化键测试内容。",
                        "title": "归一化",
                        "link": "https://example.com/normalize",
                    }
                ],
            },
        )
        with (
            patch.object(get_settings(), "zhipu_web_search_api_key", "test-key"),
            patch("app.agent.web_search.httpx.Client", _FakeHttpClient),
        ):
            first = web_search.invoke({"query": "  DeepSeek    R1 缓存 "})
            second = web_search.invoke({"query": "deepseek r1 缓存"})

        self.assertEqual(_FakeHttpClient.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "ok")

    def test_error_result_is_not_cached(self) -> None:
        _FakeHttpClient.last_response = _FakeResponse(401, {})
        with (
            patch.object(get_settings(), "zhipu_web_search_api_key", "test-key"),
            patch("app.agent.web_search.httpx.Client", _FakeHttpClient),
        ):
            first = web_search.invoke({"query": "失败不缓存"})
            second = web_search.invoke({"query": "失败不缓存"})

        self.assertEqual(_FakeHttpClient.call_count, 2)
        self.assertEqual(first["status"], "error")
        self.assertEqual(second["status"], "error")
        self.assertNotIn(
            web_search_module._normalize_query("失败不缓存"),
            web_search_module._SEARCH_CACHE,
        )

    def test_expired_cache_entry_triggers_new_request(self) -> None:
        _FakeHttpClient.last_response = _FakeResponse(
            200,
            {
                "created": 1786691538,
                "id": "req-cache-3",
                "search_intent": [],
                "search_result": [
                    {
                        "content": "过期缓存测试内容。",
                        "title": "过期",
                        "link": "https://example.com/expire",
                    }
                ],
            },
        )
        with (
            patch.object(get_settings(), "zhipu_web_search_api_key", "test-key"),
            patch("app.agent.web_search.httpx.Client", _FakeHttpClient),
        ):
            first = web_search.invoke({"query": "过期查询"})

        self.assertEqual(_FakeHttpClient.call_count, 1)

        # 直接操纵缓存：把时间戳改旧，模拟 TTL 过期。
        key = web_search_module._normalize_query("过期查询")
        with web_search_module._CACHE_LOCK:
            web_search_module._SEARCH_CACHE[key] = (
                time.monotonic() - web_search_module.WEB_SEARCH_CACHE_TTL_SECONDS - 1,
                json.dumps(first, ensure_ascii=False),
            )

        with (
            patch.object(get_settings(), "zhipu_web_search_api_key", "test-key"),
            patch("app.agent.web_search.httpx.Client", _FakeHttpClient),
        ):
            second = web_search.invoke({"query": "过期查询"})

        self.assertEqual(_FakeHttpClient.call_count, 2)
        self.assertEqual(second["status"], "ok")


if __name__ == "__main__":
    unittest.main()
