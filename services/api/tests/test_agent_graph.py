"""LangGraph 图组装与事件透传测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langgraph.config import get_stream_writer

from app.agent.checkpointer import reset_checkpointer
from app.agent.graph import _route_branch, build_graph
from app.config import get_settings


class AgentGraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._prev_path = get_settings().langgraph_checkpoint_sqlite_path
        get_settings().langgraph_checkpoint_sqlite_path = ":memory:"
        reset_checkpointer()
        build_graph.cache_clear()

    async def asyncTearDown(self) -> None:
        get_settings().langgraph_checkpoint_sqlite_path = self._prev_path
        reset_checkpointer()
        build_graph.cache_clear()

    def _config(self) -> dict:
        return {
            "configurable": {
                "thread_id": "test-graph",
                "db": None,
                "agent_run": None,
                "assistant_message": None,
                "thread": None,
            }
        }

    def test_route_branch_mapping(self) -> None:
        self.assertEqual(_route_branch({"intent": "general_chat"}), "general_chat")
        self.assertEqual(_route_branch({"intent": "schedule_intake"}), "schedule_intake")
        self.assertEqual(_route_branch({"intent": "quick_note_intake"}), "quick_note_intake")
        # needs_tool_selection 拦截门已移除，前缀原样透传（不会进入专用分支）
        self.assertEqual(
            _route_branch({"intent": "needs_tool_selection:schedule_intake"}),
            "needs_tool_selection:schedule_intake",
        )

    def test_graph_contains_all_nodes(self) -> None:
        graph = build_graph()
        nodes = set(graph.get_graph().nodes)
        self.assertLessEqual(
            {
                "intent_router",
                "general_chat",
                "schedule_intake",
                "quick_note_intake",
                "finalize",
            },
            nodes,
        )
        self.assertNotIn("tool_selection_reminder", nodes)

    async def test_custom_events_passthrough(self) -> None:
        async def fake_general_chat(_state):
            writer = get_stream_writer()
            writer({"event": "message_delta", "data": {"delta": "hi"}})
            return {"assistant_text": "hi", "created_message_ids": [], "requires_approval": None}

        with (
            patch("app.agent.graph.route_intent", return_value={"intent": "general_chat"}),
            patch("app.agent.graph.general_chat_node", side_effect=fake_general_chat),
        ):
            graph = build_graph()
            custom: list[dict] = []
            updates: list[dict] = []
            async for mode, chunk in graph.astream({}, self._config(), stream_mode=["updates", "custom"]):
                if mode == "custom":
                    custom.append(chunk)
                else:
                    updates.append(chunk)

        self.assertEqual(custom, [{"event": "message_delta", "data": {"delta": "hi"}}])
        self.assertTrue(any("finalize" in chunk for chunk in updates))

    async def test_node_error_propagates(self) -> None:
        async def boom(_state):
            raise RuntimeError("boom")

        with (
            patch("app.agent.graph.route_intent", return_value={"intent": "general_chat"}),
            patch("app.agent.graph.general_chat_node", side_effect=boom),
        ):
            graph = build_graph()
            with self.assertRaises(RuntimeError):
                async for _ in graph.astream({}, self._config(), stream_mode="updates"):
                    pass


if __name__ == "__main__":
    unittest.main()
