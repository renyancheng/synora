"""外部 MCP server 配置转换、客户端聚合与工具降级测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agent.external_mcp import McpServerSettings
from app.agent.tools import build_agent_tools
from app.config import get_settings
from app.runtime.mcp_client import get_mcp_client


class ExternalMcpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._prev_servers = get_settings().mcp_servers
        get_settings().mcp_servers = []
        get_mcp_client.cache_clear()

    def tearDown(self) -> None:
        get_settings().mcp_servers = self._prev_servers
        get_mcp_client.cache_clear()

    def test_to_adapter_config_streamable_http(self) -> None:
        cfg = McpServerSettings(
            name="filesystem",
            transport="streamable_http",
            url="http://fs:8000/mcp",
            headers={"Authorization": "Bearer x"},
        )
        out = cfg.to_adapter_config()
        self.assertEqual(out["transport"], "streamable_http")
        self.assertEqual(out["url"], "http://fs:8000/mcp")
        self.assertEqual(out["headers"], {"Authorization": "Bearer x"})

    def test_to_adapter_config_stdio(self) -> None:
        cfg = McpServerSettings(
            name="local",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        out = cfg.to_adapter_config()
        self.assertEqual(out["transport"], "stdio")
        self.assertEqual(out["command"], "npx")
        self.assertEqual(out["args"], ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"])

    def test_get_mcp_client_includes_external_servers(self) -> None:
        get_settings().mcp_servers = [
            McpServerSettings(name="ext", transport="streamable_http", url="http://ext/mcp")
        ]
        client = get_mcp_client()
        self.assertIn("synora", client.connections)
        self.assertIn("ext", client.connections)

    async def test_build_agent_tools_degrades_unavailable_server(self) -> None:
        get_settings().mcp_servers = [
            McpServerSettings(name="ext", transport="streamable_http", url="http://ext/mcp")
        ]

        async def fake_get_tools(*, server_name: str | None = None):
            if server_name == "ext":
                raise ConnectionError("server down")
            return ["tool-a"]

        mock_client = unittest.mock.MagicMock()
        mock_client.get_tools = fake_get_tools
        with patch("app.agent.tools.get_mcp_client", return_value=mock_client):
            tools = await build_agent_tools()

        self.assertEqual(tools, ["tool-a"])


if __name__ == "__main__":
    unittest.main()
