"""LangGraph 图组装、循环边界与事件透传测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langgraph.config import get_stream_writer

from app.agent.checkpointer import reset_checkpointer
from app.agent.graph import _route_branch, _route_loop, build_graph
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
            }
        }

    def test_graph_config_contains_only_stable_thread_id(self) -> None:
        self.assertEqual(set(self._config()["configurable"]), {"thread_id"})

    def test_route_branch_mapping(self) -> None:
        self.assertEqual(_route_branch({"intent": "general_chat"}), "general_chat")
        self.assertEqual(_route_branch({"intent": "schedule_intake"}), "schedule_intake")
        self.assertEqual(_route_branch({"intent": "quick_note_intake"}), "quick_note_intake")
        # needs_tool_selection 拦截门已移除，前缀原样透传（不会进入专用分支）
        self.assertEqual(
            _route_branch({"intent": "needs_tool_selection:schedule_intake"}),
            "needs_tool_selection:schedule_intake",
        )

    def test_route_loop_termination(self) -> None:
        """iteration 未达上限回 act；等于上限、或 decision=done 均收口 finalize。"""
        self.assertEqual(
            _route_loop({"loop_decision": "continue", "iteration_count": 3, "max_iterations": 4}),
            "act",
        )
        self.assertEqual(
            _route_loop({"loop_decision": "continue", "iteration_count": 4, "max_iterations": 4}),
            "finalize",
        )
        self.assertEqual(
            _route_loop({"loop_decision": "done", "iteration_count": 0, "max_iterations": 4}),
            "finalize",
        )
        # 无 loop_decision（异常态）保守收口
        self.assertEqual(_route_loop({"iteration_count": 5, "max_iterations": 4}), "finalize")

    def test_graph_contains_all_nodes(self) -> None:
        graph = build_graph()
        nodes = set(graph.get_graph().nodes)
        self.assertLessEqual(
            {
                "intent_router",
                "plan",
                "act",
                "observe",
                "reflect",
                "schedule_intake",
                "quick_note_intake",
                "finalize",
            },
            nodes,
        )
        self.assertNotIn("general_chat", nodes)
        self.assertNotIn("tool_selection_reminder", nodes)

    async def test_loop_iterations_bounded(self) -> None:
        """reflect 恒 continue 时，act 次数被 max_iterations 硬限。"""
        act_calls = {"count": 0}

        async def fake_route_intent(_state):
            return {"intent": "general_chat"}

        async def fake_plan(_state):
            return {
                "plan": "p",
                "iteration_count": 0,
                "max_iterations": 4,
                "loop_decision": "continue",
                "reasoning_steps": [],
            }

        async def fake_act(state):
            act_calls["count"] += 1
            return {
                "iteration_count": int(state.get("iteration_count") or 0) + 1,
                "assistant_text": "hi",
                "agent_messages": [],
                "pending_tool_calls": [],
                "current_aimessage": None,
                "reasoning_steps": [],
            }

        async def fake_observe(_state):
            return {
                "agent_messages": [],
                "observation": "",
                "pending_tool_calls": [],
                "reasoning_steps": [],
            }

        async def fake_reflect(_state):
            return {
                "loop_decision": "continue",
                "reflection": "",
                "follow_up_prompt": None,
                "reasoning_steps": [],
            }

        with (
            patch("app.agent.graph.route_intent", side_effect=fake_route_intent),
            patch("app.agent.graph.plan_node", side_effect=fake_plan),
            patch("app.agent.graph.act_node", side_effect=fake_act),
            patch("app.agent.graph.observe_node", side_effect=fake_observe),
            patch("app.agent.graph.reflect_node", side_effect=fake_reflect),
        ):
            build_graph.cache_clear()
            graph = build_graph()
            async for _mode, _chunk in graph.astream({}, self._config(), stream_mode=["updates", "custom"]):
                pass
            build_graph.cache_clear()

        self.assertEqual(act_calls["count"], 4)

    async def test_custom_events_passthrough(self) -> None:
        async def fake_plan(_state):
            writer = get_stream_writer()
            writer({"event": "message_delta", "data": {"delta": "hi"}})
            return {
                "plan": "p",
                "iteration_count": 0,
                "max_iterations": 4,
                "loop_decision": "continue",
                "reasoning_steps": [],
            }

        async def fake_act(_state):
            return {
                "iteration_count": 1,
                "assistant_text": "hi",
                "agent_messages": [],
                "pending_tool_calls": [],
                "current_aimessage": None,
                "reasoning_steps": [],
            }

        async def fake_observe(_state):
            return {
                "agent_messages": [],
                "observation": "",
                "pending_tool_calls": [],
                "reasoning_steps": [],
            }

        async def fake_reflect(_state):
            return {
                "loop_decision": "done",
                "reflection": "",
                "follow_up_prompt": None,
                "reasoning_steps": [],
            }

        with (
            patch("app.agent.graph.route_intent", return_value={"intent": "general_chat"}),
            patch("app.agent.graph.plan_node", side_effect=fake_plan),
            patch("app.agent.graph.act_node", side_effect=fake_act),
            patch("app.agent.graph.observe_node", side_effect=fake_observe),
            patch("app.agent.graph.reflect_node", side_effect=fake_reflect),
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
            patch("app.agent.graph.plan_node", side_effect=boom),
        ):
            graph = build_graph()
            with self.assertRaises(RuntimeError):
                async for _ in graph.astream({}, self._config(), stream_mode="updates"):
                    pass


if __name__ == "__main__":
    unittest.main()
