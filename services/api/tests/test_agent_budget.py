"""P0-2 运行级预算测试：总时长预算、单轮 token 上限、总 token 预算声明与路由收口。"""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agent import checkpointer as checkpointer_module
from app.agent.graph import _route_loop, build_graph
from app.agent.llm import create_chat_model
from app.agent.nodes import act_node, reflect_node
from app.config import Settings, get_settings
from app.db import Base
from app.models import AgentRun, ConversationMessage, ConversationThread, User


class AgentBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # 测试强制 sqlite :memory: checkpointer，隔离 dev postgres checkpoint
        self._prev_ckpt_backend = get_settings().langgraph_checkpoint_backend
        self._prev_ckpt_path = get_settings().langgraph_checkpoint_sqlite_path
        get_settings().langgraph_checkpoint_backend = "sqlite"
        get_settings().langgraph_checkpoint_sqlite_path = ":memory:"
        checkpointer_module.reset_checkpointer()
        build_graph.cache_clear()
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
        Base.metadata.create_all(self.engine)
        self.db = self.session_factory()
        self._nodes_session_patch = patch("app.agent.nodes.SessionLocal", side_effect=self.session_factory)
        self._nodes_session_patch.start()
        self.user = User(
            email="budget@example.com",
            display_name="预算测试",
            password_hash="hashed-password",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self) -> None:
        self.db.close()
        self._nodes_session_patch.stop()
        self.engine.dispose()
        get_settings().langgraph_checkpoint_backend = self._prev_ckpt_backend
        get_settings().langgraph_checkpoint_sqlite_path = self._prev_ckpt_path
        checkpointer_module.reset_checkpointer()
        build_graph.cache_clear()

    def _make_resources(self) -> tuple[ConversationThread, AgentRun, ConversationMessage]:
        """创建 _load_resources 所需的 thread / agent_run / assistant_message 三件套。"""
        thread = ConversationThread(user_id=self.user.id, title="预算测试")
        self.db.add(thread)
        self.db.commit()
        self.db.refresh(thread)
        assistant_message = ConversationMessage(
            conversation_id=thread.id,
            role="assistant",
            message_type="text",
            status="streaming",
            text_content="",
            structured_payload_json={},
        )
        self.db.add(assistant_message)
        self.db.commit()
        self.db.refresh(assistant_message)
        agent_run = AgentRun(
            user_id=self.user.id,
            workflow="general_chat",
            status="running",
            conversation_id=thread.id,
        )
        self.db.add(agent_run)
        self.db.commit()
        self.db.refresh(agent_run)
        return thread, agent_run, assistant_message

    # ---- _route_loop ----

    def test_route_loop_budget_exhausted_finalizes(self) -> None:
        """budget_exhausted 为真直接收口（即使仍在轮次上限内且 decision=continue）。"""
        self.assertEqual(
            _route_loop(
                {
                    "budget_exhausted": True,
                    "loop_decision": "continue",
                    "iteration_count": 0,
                    "max_iterations": 4,
                }
            ),
            "finalize",
        )

    def test_route_loop_normal_continue(self) -> None:
        """无预算命中且轮次未超时正常回 act。"""
        self.assertEqual(
            _route_loop({"loop_decision": "continue", "iteration_count": 1, "max_iterations": 4}),
            "act",
        )

    def test_route_loop_iteration_limit_finalizes(self) -> None:
        self.assertEqual(
            _route_loop({"loop_decision": "continue", "iteration_count": 4, "max_iterations": 4}),
            "finalize",
        )

    def test_route_loop_token_budget_finalizes(self) -> None:
        """总 token 预算命中时 finalize；total_tokens=0（尚未记账）不触发判断。"""
        with patch("app.agent.graph.get_settings", return_value=SimpleNamespace(agent_max_run_tokens=100)):
            self.assertEqual(
                _route_loop(
                    {
                        "loop_decision": "continue",
                        "iteration_count": 1,
                        "max_iterations": 4,
                        "total_tokens": 150,
                    }
                ),
                "finalize",
            )
            self.assertEqual(
                _route_loop(
                    {
                        "loop_decision": "continue",
                        "iteration_count": 1,
                        "max_iterations": 4,
                        "total_tokens": 0,
                    }
                ),
                "act",
            )

    # ---- reflect_node ----

    async def test_reflect_node_overrides_continue_to_done_when_budget_exhausted(self) -> None:
        """reflect_step 返回 continue 但 state.budget_exhausted 为真时覆盖为 done。"""
        thread, agent_run, assistant_message = self._make_resources()
        state = {
            "conversation_id": thread.id,
            "agent_run_id": agent_run.id,
            "assistant_message_id": assistant_message.id,
            "budget_exhausted": True,
            "iteration_count": 1,
        }
        with (
            patch("app.agent.nodes.get_config", return_value={"configurable": {"thread_id": "test"}}),
            patch("app.agent.nodes.get_stream_writer", return_value=lambda _event: None),
            patch("app.agent.nodes.reflect_step", new_callable=AsyncMock) as reflect_mock,
        ):
            reflect_mock.return_value = {
                "loop_decision": "continue",
                "reflection": "工具已执行，需基于工具结果生成最终回答",
                "follow_up_prompt": "继续",
                "anti_repeat_used": False,
                "anti_empty_retries": 0,
                "anti_commitment_used": False,
                "steps": [],
            }
            result = await reflect_node(state)

        self.assertEqual(result["loop_decision"], "done")
        self.assertEqual(result["reflection"], "已达运行时长预算，安全收口")
        reflect_mock.assert_awaited_once()

    async def test_reflect_node_preserves_done_without_budget_exhausted(self) -> None:
        """未命中预算时 reflect_node 不应改动 reflect_step 的收口结果。"""
        thread, agent_run, assistant_message = self._make_resources()
        state = {
            "conversation_id": thread.id,
            "agent_run_id": agent_run.id,
            "assistant_message_id": assistant_message.id,
            "budget_exhausted": False,
            "iteration_count": 1,
        }
        with (
            patch("app.agent.nodes.get_config", return_value={"configurable": {"thread_id": "test"}}),
            patch("app.agent.nodes.get_stream_writer", return_value=lambda _event: None),
            patch("app.agent.nodes.reflect_step", new_callable=AsyncMock) as reflect_mock,
        ):
            reflect_mock.return_value = {
                "loop_decision": "done",
                "reflection": "本轮无工具调用，回答完整",
                "follow_up_prompt": None,
                "anti_repeat_used": False,
                "anti_empty_retries": 0,
                "anti_commitment_used": False,
                "steps": [],
            }
            result = await reflect_node(state)

        self.assertEqual(result["loop_decision"], "done")
        self.assertEqual(result["reflection"], "本轮无工具调用，回答完整")

    # ---- create_chat_model 单轮 token 上限 ----

    def test_create_chat_model_injects_max_tokens_per_round(self) -> None:
        settings = Settings(llm_api_key="sk-test", llm_model="qwen-test", agent_max_tokens_per_round=512)
        with patch("app.agent.llm.ChatOpenAI") as chat_mock:
            create_chat_model(settings, temperature=0.6)
        self.assertEqual(chat_mock.call_args.kwargs["max_tokens"], 512)

    def test_create_chat_model_explicit_max_tokens_not_overridden(self) -> None:
        settings = Settings(llm_api_key="sk-test", llm_model="qwen-test", agent_max_tokens_per_round=512)
        with patch("app.agent.llm.ChatOpenAI") as chat_mock:
            create_chat_model(settings, temperature=0.6, max_tokens=1024)
        self.assertEqual(chat_mock.call_args.kwargs["max_tokens"], 1024)

    def test_create_chat_model_zero_setting_does_not_inject(self) -> None:
        settings = Settings(llm_api_key="sk-test", llm_model="qwen-test", agent_max_tokens_per_round=0)
        with patch("app.agent.llm.ChatOpenAI") as chat_mock:
            create_chat_model(settings, temperature=0.6)
        self.assertNotIn("max_tokens", chat_mock.call_args.kwargs)

    # ---- act_node 总时长预算 ----

    async def test_act_node_skips_step_when_time_budget_exhausted(self) -> None:
        """时长预算已用尽时 act_node 跳过 act_step、写入失败步骤并置 budget_exhausted。"""
        thread, agent_run, assistant_message = self._make_resources()
        events: list[dict] = []
        state = {
            "conversation_id": thread.id,
            "agent_run_id": agent_run.id,
            "assistant_message_id": assistant_message.id,
            "iteration_count": 2,
            "run_started_at": time.monotonic() - 1000.0,
            "reasoning_steps": [],
        }
        with (
            patch("app.agent.nodes.get_config", return_value={"configurable": {"thread_id": "test"}}),
            patch("app.agent.nodes.get_stream_writer", return_value=events.append),
            patch("app.agent.nodes.get_settings", return_value=SimpleNamespace(agent_max_run_seconds=1)),
            patch("app.agent.nodes.act_step", new_callable=AsyncMock) as act_mock,
        ):
            result = await act_node(state)

        act_mock.assert_not_awaited()
        self.assertTrue(result["budget_exhausted"])
        self.assertEqual(result["agent_messages"], [])
        self.assertEqual(result["pending_tool_calls"], [])
        self.assertEqual(result["iteration_count"], 2)
        self.assertEqual(len(result["reasoning_steps"]), 1)
        step = result["reasoning_steps"][0]
        self.assertEqual(step["step_type"], "act")
        self.assertEqual(step["status"], "failed")
        self.assertIn("运行时长预算已用尽", step["content"])
        self.assertEqual(events[0]["event"], "reasoning_step")


if __name__ == "__main__":
    unittest.main()
