"""Agent 主循环优化测试：工具并行调度 / 上下文记账与压缩 / 可观测性接线。

覆盖：工具并发执行与限流、结果顺序保持、单工具失败隔离、记忆只检索一次、
超长工具结果截断（完整结果仍进审计）、工具耗时与 token 记账持久化、
以及 general_chat 全图冒烟回归。
"""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent import checkpointer as checkpointer_module, llm
from app.agent.graph import build_graph
from app.config import get_settings
from app.db import Base
from app.domains.conversation.agent_service import (
    GENERAL_CHAT_TOOL_CONCURRENCY,
    TOOL_RESULT_MAX_CHARS,
    TOOL_RESULT_TRUNCATED_SUFFIX,
    act_step,
    observe_step,
)
from app.domains.conversation.service import consume_stream, create_conversation, queue_message
from app.models import AgentRun, AgentToolCallAudit, ConversationMessage, ConversationThread, User
from app.schemas.conversation import ConversationSendMessageRequest


def _fake_ainvoke_structured(settings, **kwargs):
    """图路径 mock：plan/reflect 的结构化调用按 operation 返回固定结果。"""
    operation = kwargs.get("operation")
    if operation == "agent_plan":
        return llm.PlanResult(plan="回答用户")
    if operation == "agent_reflect":
        return llm.ReflectDecision(is_complete=True, rationale="信息已充分")
    raise AssertionError(f"unexpected ainvoke_structured operation: {operation}")


class _FakeStreamingChatModel:
    """纯文本流式模型：bind_tools 直通，astream 产单个 AIMessageChunk。"""

    def __init__(self, text: str = "好的，我来帮你一起整理。") -> None:
        self._text = text

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        from langchain_core.messages import AIMessageChunk

        yield AIMessageChunk(content=self._text)


class AgentLoopOptimizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # 与 test_conversation_service 一致：sqlite :memory: checkpointer 隔离 + 图缓存清理。
        self._prev_ckpt_backend = get_settings().langgraph_checkpoint_backend
        self._prev_ckpt_path = get_settings().langgraph_checkpoint_sqlite_path
        get_settings().langgraph_checkpoint_backend = "sqlite"
        get_settings().langgraph_checkpoint_sqlite_path = ":memory:"
        checkpointer_module.reset_checkpointer()
        build_graph.cache_clear()
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)
        Base.metadata.create_all(self.engine)
        self.db = self.session_factory()
        self._nodes_session_patch = patch("app.agent.nodes.SessionLocal", side_effect=self.session_factory)
        self._nodes_session_patch.start()
        self.user = User(
            email="opt@example.com",
            display_name="优化测试",
            password_hash="hashed-password",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.thread = ConversationThread(user_id=self.user.id, title="优化测试")
        self.db.add(self.thread)
        self.db.commit()
        self.message = ConversationMessage(
            conversation_id=self.thread.id,
            role="assistant",
            message_type="text",
            status="streaming",
            text_content="",
            structured_payload_json={},
        )
        self.db.add(self.message)
        self.db.commit()
        self.events: list[dict] = []

    def tearDown(self) -> None:
        self.db.close()
        self._nodes_session_patch.stop()
        self.engine.dispose()
        get_settings().langgraph_checkpoint_backend = self._prev_ckpt_backend
        get_settings().langgraph_checkpoint_sqlite_path = self._prev_ckpt_path
        checkpointer_module.reset_checkpointer()
        build_graph.cache_clear()

    def _state(self, **overrides) -> dict:
        state = {
            "user_message": "现在几点了",
            "reasoning_steps": [],
            "agent_run_id": 1,
            "conversation_id": self.thread.id,
            "stream_id": None,
        }
        state.update(overrides)
        return state

    async def test_parallel_tool_execution_is_faster_than_serial(self) -> None:
        """两个各睡 0.2s 的工具并发执行，总耗时显著小于串行（< 0.35s）。"""

        class _SleepTool:
            name = "sleepy"

            def __init__(self, delay: float = 0.2) -> None:
                self._delay = delay

            async def ainvoke(self, args):
                await asyncio.sleep(self._delay)
                return "done"

        tool_calls = [
            {"name": "sleepy", "args": {}, "id": "c1"},
            {"name": "sleepy", "args": {}, "id": "c2"},
        ]
        with patch(
            "app.domains.conversation.agent_service.build_agent_tools",
            new_callable=AsyncMock,
            return_value=[_SleepTool(0.2)],
        ):
            started = time.monotonic()
            result = await observe_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(id=1),
                state=self._state(pending_tool_calls=tool_calls),
                emit=self.events.append,
            )
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.35)
        self.assertEqual(len(result["tool_messages"]), 2)
        self.assertFalse(result["tool_failed"])

    async def test_tool_concurrency_peak_is_bounded(self) -> None:
        """并发峰值 ≤ 并发常量（假工具内计数）。"""
        probe = {"current": 0, "peak": 0}

        class _ProbeTool:
            name = "probe"

            def __init__(self, probe: dict) -> None:
                self._probe = probe

            async def ainvoke(self, args):
                self._probe["current"] += 1
                self._probe["peak"] = max(self._probe["peak"], self._probe["current"])
                await asyncio.sleep(0.05)
                self._probe["current"] -= 1
                return "ok"

        tool_calls = [{"name": "probe", "args": {}, "id": f"c{i}"} for i in range(8)]
        with patch(
            "app.domains.conversation.agent_service.build_agent_tools",
            new_callable=AsyncMock,
            return_value=[_ProbeTool(probe)],
        ):
            await observe_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(id=1),
                state=self._state(pending_tool_calls=tool_calls),
                emit=self.events.append,
            )

        self.assertLessEqual(probe["peak"], GENERAL_CHAT_TOOL_CONCURRENCY)
        self.assertGreater(probe["peak"], 1)

    async def test_tool_messages_preserve_tool_calls_order(self) -> None:
        """结果回填必须保持原始调用顺序，与并发完成顺序无关。"""

        class _NamedTool:
            def __init__(self, name: str, result: str, delay: float = 0.0) -> None:
                self.name = name
                self._result = result
                self._delay = delay

            async def ainvoke(self, args):
                if self._delay:
                    await asyncio.sleep(self._delay)
                return self._result

        # alpha 更慢，若按完成顺序回填 beta 会先到，这里必须仍保持 alpha → beta。
        tools = [_NamedTool("alpha", "result-alpha", delay=0.15), _NamedTool("beta", "result-beta", delay=0.0)]
        tool_calls = [{"name": "alpha", "args": {}, "id": "c1"}, {"name": "beta", "args": {}, "id": "c2"}]
        with patch(
            "app.domains.conversation.agent_service.build_agent_tools",
            new_callable=AsyncMock,
            return_value=tools,
        ):
            result = await observe_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(id=1),
                state=self._state(pending_tool_calls=tool_calls),
                emit=self.events.append,
            )

        self.assertEqual([m["name"] for m in result["tool_messages"]], ["alpha", "beta"])
        self.assertEqual([m["content"] for m in result["tool_messages"]], ["result-alpha", "result-beta"])

    async def test_single_tool_failure_isolated(self) -> None:
        """一个抛异常、另一个成功：成功者结果在列，tool_failed 真但 tool_failed_all 假。"""

        class _FailingTool:
            name = "boom"

            async def ainvoke(self, args):
                raise RuntimeError("boom-failed")

        class _OkTool:
            name = "ok"

            async def ainvoke(self, args):
                return "fine"

        tool_calls = [{"name": "boom", "args": {}, "id": "c1"}, {"name": "ok", "args": {}, "id": "c2"}]
        with patch(
            "app.domains.conversation.agent_service.build_agent_tools",
            new_callable=AsyncMock,
            return_value=[_FailingTool(), _OkTool()],
        ):
            result = await observe_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(id=1),
                state=self._state(pending_tool_calls=tool_calls),
                emit=self.events.append,
            )

        self.assertTrue(result["tool_failed"])
        self.assertFalse(result["tool_failed_all"])
        self.assertEqual(result["tool_messages"][0]["content"], "工具执行失败：boom-failed")
        self.assertEqual(result["tool_messages"][1]["content"], "fine")
        audits = self.db.scalars(select(AgentToolCallAudit).order_by(AgentToolCallAudit.id)).all()
        self.assertEqual([audit.status for audit in audits], ["failed", "ok"])

    async def test_memory_retrieved_once_across_act_rounds(self) -> None:
        """两轮 act 时 MemoryService.retrieve_context 仅调用一次。"""
        calls = {"n": 0}

        def _retrieve(*args, **kwargs):
            calls["n"] += 1
            return SimpleNamespace(summary="", items=[])

        class _Model:
            def bind_tools(self, tools):
                return self

            async def astream(self, messages):
                yield SimpleNamespace(content="ok", tool_calls=[], tool_call_chunks=[])

        with (
            patch("app.domains.conversation.agent_service.MemoryService.retrieve_context", side_effect=_retrieve),
            patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[]),
            patch("app.agent.llm.create_chat_model", return_value=_Model()),
        ):
            first = await act_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(),
                state=self._state(user_message="hi"),
                emit=self.events.append,
            )
            second = await act_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(),
                state=self._state(user_message="hi", iteration_count=1, memory_payload=first["memory_payload"]),
                emit=self.events.append,
            )

        self.assertEqual(calls["n"], 1)
        self.assertEqual(second["memory_payload"], first["memory_payload"])

    async def test_long_tool_result_truncated_but_audit_keeps_full(self) -> None:
        """超长工具结果截断进 ToolMessage，完整结果仍写入审计 response_json。"""
        full_text = "L" * 2000

        class _LongTool:
            name = "longtool"

            async def ainvoke(self, args):
                return full_text

        with patch(
            "app.domains.conversation.agent_service.build_agent_tools",
            new_callable=AsyncMock,
            return_value=[_LongTool()],
        ):
            result = await observe_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(id=1),
                state=self._state(pending_tool_calls=[{"name": "longtool", "args": {}, "id": "c1"}]),
                emit=self.events.append,
            )

        content = result["tool_messages"][0]["content"]
        self.assertLessEqual(len(content), TOOL_RESULT_MAX_CHARS + len(TOOL_RESULT_TRUNCATED_SUFFIX))
        self.assertTrue(content.endswith(TOOL_RESULT_TRUNCATED_SUFFIX))
        audit = self.db.scalars(select(AgentToolCallAudit)).one()
        self.assertEqual(audit.response_json, full_text)

    async def test_tool_audit_latency_ms_written(self) -> None:
        class _Tool:
            name = "quick"

            async def ainvoke(self, args):
                return "ok"

        with patch(
            "app.domains.conversation.agent_service.build_agent_tools",
            new_callable=AsyncMock,
            return_value=[_Tool()],
        ):
            await observe_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(id=1),
                state=self._state(pending_tool_calls=[{"name": "quick", "args": {}, "id": "c1"}]),
                emit=self.events.append,
            )

        audit = self.db.scalars(select(AgentToolCallAudit)).one()
        self.assertIsNotNone(audit.latency_ms)
        self.assertGreaterEqual(audit.latency_ms, 0)

    async def test_act_step_records_tokens_and_persists_metrics(self) -> None:
        agent_run = AgentRun(
            user_id=self.thread.user_id,
            workflow="general_chat",
            status="running",
            conversation_id=self.thread.id,
            stream_token="tok-metrics",
            input_json={},
            output_json={},
        )
        self.db.add(agent_run)
        self.db.commit()

        class _UsageModel:
            def bind_tools(self, tools):
                return self

            async def astream(self, messages):
                yield SimpleNamespace(
                    content="回答",
                    tool_calls=[],
                    tool_call_chunks=[],
                    usage_metadata={"input_tokens": 12, "output_tokens": 34, "total_tokens": 46},
                )

        with (
            patch("app.domains.conversation.agent_service.MemoryService.retrieve_context", return_value=SimpleNamespace(summary="", items=[])),
            patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[]),
            patch("app.agent.llm.create_chat_model", return_value=_UsageModel()),
        ):
            result = await act_step(
                self.db,
                self.thread,
                self.message,
                agent_run,
                state=self._state(user_message="hi"),
                emit=self.events.append,
            )

        self.assertEqual(result["round_tokens"]["prompt_tokens"], 12)
        self.assertEqual(result["round_tokens"]["completion_tokens"], 34)
        self.assertGreaterEqual(result["round_tokens"]["latency_ms"], 0)
        self.db.expire_all()
        refreshed = self.db.get(AgentRun, agent_run.id)
        self.assertEqual(refreshed.total_tokens, 46)
        self.assertEqual(len(refreshed.step_metrics), 1)
        self.assertEqual(refreshed.step_metrics[0]["prompt_tokens"], 12)
        self.assertEqual(refreshed.step_metrics[0]["completion_tokens"], 34)

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model", return_value=_FakeStreamingChatModel(text="好的，我来帮你一起整理。"))
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured)
    @patch("app.agent.llm.generate_conversation_title", return_value="优化问答")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_general_chat_graph_run_completes(
        self,
        _extract_mock,
        memory_mock,
        _intent_mock,
        _title_mock,
        _ainvoke_mock,
        _chat_model_mock,
        _tools_mock,
        _write_memory_mock,
    ) -> None:
        """回归冒烟：一个简单 general_chat 全图消息跑通到 run_completed。"""
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好，帮我看看今天安排"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(events[0]["event"], "run_started")
        self.assertEqual(events[-1]["event"], "run_completed")


if __name__ == "__main__":
    unittest.main()
