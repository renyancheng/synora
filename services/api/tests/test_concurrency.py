"""P1-2 Agent 运行并发与优先级调度测试。

覆盖：
- 全局并发闸门：并发上限生效（第 N+1 个 run 等待而非立即执行）。
- intake 优先：闸门被 general_chat 占满时 intake 仍能进入执行。
- 取消等待：等待闸门期间用户取消不会悬挂。
- 每会话单 run 串行化（同一 conversation 第二个 run 保持 pending 等待）。
- 已有行为回归：graph 路径简单对话冒烟。
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent import checkpointer as checkpointer_module
from app.agent import llm
from app.agent.graph import build_graph
from app.config import get_settings
from app.db import Base
from app.domains.conversation.service import (
    _PriorityRunGate,
    _reset_concurrency_state,
    abort_stream,
    consume_stream,
    create_conversation,
    queue_message,
)
from app.models import AgentRun, ConversationMessage, User
from app.schemas.conversation import ConversationSendMessageRequest


class _FakeStreamingChatModel:
    """graph 路径冒烟用：astream 直接产出一段最终文本。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        from langchain_core.messages import AIMessageChunk

        yield AIMessageChunk(content=self._text)


async def _fake_ainvoke_structured(settings, **kwargs):
    operation = kwargs.get("operation")
    if operation == "agent_plan":
        return llm.PlanResult(plan="回答用户")
    if operation == "agent_reflect":
        return llm.ReflectDecision(is_complete=True, rationale="信息已充分")
    raise AssertionError(f"unexpected ainvoke_structured operation: {operation}")


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # 测试强制 sqlite :memory: checkpointer，隔离 dev postgres checkpoint。
        self._prev_ckpt_backend = get_settings().langgraph_checkpoint_backend
        self._prev_ckpt_path = get_settings().langgraph_checkpoint_sqlite_path
        self._prev_backend = get_settings().agent_backend
        self._prev_max_concurrent = get_settings().agent_max_concurrent_runs
        self._prev_intake = get_settings().agent_max_intake_concurrent_runs
        self._prev_general = get_settings().agent_max_general_chat_concurrent_runs
        self._prev_reserved = get_settings().agent_intake_reserved_slots
        get_settings().langgraph_checkpoint_backend = "sqlite"
        get_settings().langgraph_checkpoint_sqlite_path = ":memory:"
        checkpointer_module.reset_checkpointer()
        build_graph.cache_clear()
        _reset_concurrency_state()
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)
        Base.metadata.create_all(self.engine)
        self.db = self.session_factory()
        self._nodes_session_patch = patch("app.agent.nodes.SessionLocal", side_effect=self.session_factory)
        self._nodes_session_patch.start()
        self.user = User(
            email="han.teacher@example.com",
            display_name="韩老师",
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
        get_settings().agent_backend = self._prev_backend
        get_settings().agent_max_concurrent_runs = self._prev_max_concurrent
        get_settings().agent_max_intake_concurrent_runs = self._prev_intake
        get_settings().agent_max_general_chat_concurrent_runs = self._prev_general
        get_settings().agent_intake_reserved_slots = self._prev_reserved
        checkpointer_module.reset_checkpointer()
        build_graph.cache_clear()
        _reset_concurrency_state()

    async def _collect(self, db: Session, thread, agent_run) -> list[dict]:
        return [item async for item in consume_stream(db, self.user.id, thread.id, agent_run.stream_token)]

    async def test_gate_limits_concurrent_runs(self) -> None:
        """全局闸门：上限=2 时同时进入闸门的 run 数峰值不超过 2。"""
        gate = _PriorityRunGate(total=2, general=2, intake=2)
        active = {"current": 0, "peak": 0}

        async def worker() -> None:
            await gate.acquire(is_intake=False)
            active["current"] += 1
            active["peak"] = max(active["peak"], active["current"])
            try:
                await asyncio.sleep(0.05)
            finally:
                active["current"] -= 1
                gate.release(is_intake=False)

        await asyncio.gather(*[worker() for _ in range(6)])
        self.assertEqual(active["peak"], 2)

    async def test_gate_intake_priority_when_general_full(self) -> None:
        """闸门被 general_chat 占满时，intake 仍能从剩余全局槽位进入执行。"""
        gate = _PriorityRunGate(total=2, general=1, intake=2)
        general_entered = asyncio.Event()
        release_general = asyncio.Event()

        async def hold_general() -> None:
            await gate.acquire(is_intake=False)
            general_entered.set()
            await release_general.wait()
            gate.release(is_intake=False)

        async def block_general() -> None:
            # general 配额已满，此任务应阻塞在 general 信号量上（不占 total 槽位）。
            await gate.acquire(is_intake=False)
            gate.release(is_intake=False)

        holder = asyncio.create_task(hold_general())
        await general_entered.wait()

        blocker = asyncio.create_task(block_general())
        await asyncio.sleep(0.05)

        acquired = {"ok": False}

        async def intake() -> None:
            await gate.acquire(is_intake=True)
            acquired["ok"] = True
            gate.release(is_intake=True)

        await asyncio.wait_for(intake(), timeout=1.0)
        self.assertTrue(acquired["ok"])
        self.assertFalse(blocker.done())

        release_general.set()
        await asyncio.gather(holder, blocker)

    async def test_consume_stream_respects_global_gate(self) -> None:
        """consume_stream 集成：全局上限=2 时并发峰值不超过 2（第 3 个等待）。"""
        get_settings().agent_backend = "legacy"
        get_settings().agent_max_concurrent_runs = 2
        get_settings().agent_intake_reserved_slots = 0
        _reset_concurrency_state()
        active = {"current": 0, "peak": 0}

        async def slow_general(db, thread, assistant_message, agent_run, *, user_message, attachment_parts, conversation_history_lines=None, stream_id=None):
            active["current"] += 1
            active["peak"] = max(active["peak"], active["current"])
            try:
                await asyncio.sleep(0.1)
                assistant_message.text_content = "好的"
                yield {"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": "好的"}}
            finally:
                active["current"] -= 1

        async def run_one(text: str):
            db = self.session_factory()
            try:
                thread = create_conversation(db, self.user.id)
                _, _, _, agent_run = queue_message(db, self.user.id, thread.id, ConversationSendMessageRequest(text_content=text))
                events = [item async for item in consume_stream(db, self.user.id, thread.id, agent_run.stream_token)]
                return events
            finally:
                db.close()

        with patch("app.domains.conversation.service.stream_general_chat", side_effect=slow_general), \
             patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat"), \
             patch("app.agent.llm.generate_conversation_title", return_value="标题"):
            results = await asyncio.gather(*[run_one(f"问题 {i}") for i in range(5)])

        self.assertEqual(active["peak"], 2)
        self.assertTrue(all(events[-1]["event"] == "run_completed" for events in results))

    async def test_intake_priority_through_consume_stream(self) -> None:
        """consume_stream 集成：general_chat 占满闸门时 intake 仍能进入并完成。"""
        get_settings().agent_backend = "legacy"
        get_settings().agent_max_concurrent_runs = 2
        get_settings().agent_intake_reserved_slots = 1
        _reset_concurrency_state()

        general_entered = asyncio.Event()
        release_general = asyncio.Event()

        async def slow_general(db, thread, assistant_message, agent_run, *, user_message, attachment_parts, conversation_history_lines=None, stream_id=None):
            general_entered.set()
            await release_general.wait()
            assistant_message.text_content = "好的"
            yield {"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": "好的"}}

        async def fake_intent(settings, payload, attachment_parts=None):
            if payload.get("selected_tool") == "schedule":
                return "schedule_intake"
            return "general_chat"

        async def fake_schedule_intake(db, user_id, thread, agent_run, *, text_content, attachment_ids, context, action_group_id=None, revision=1, stream_id=None):
            return "好的，日程已记录。", [], None, []

        async def run(db, text, selected_tool=None):
            thread = create_conversation(db, self.user.id)
            _, _, _, agent_run = queue_message(db, self.user.id, thread.id, ConversationSendMessageRequest(text_content=text, selected_tool=selected_tool))
            return thread, agent_run, await self._collect(db, thread, agent_run)

        with patch("app.domains.conversation.service.stream_general_chat", side_effect=slow_general), \
             patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, side_effect=fake_intent), \
             patch("app.domains.conversation.service.process_schedule_intake", new_callable=AsyncMock, side_effect=fake_schedule_intake), \
             patch("app.agent.llm.generate_conversation_title", return_value="标题"):
            db_a = self.session_factory()
            db_b = self.session_factory()
            db_c = self.session_factory()
            try:
                task_a = asyncio.create_task(run(db_a, "普通问题 A"))
                await general_entered.wait()  # A 已占满 general 配额

                task_b = asyncio.create_task(run(db_b, "普通问题 B"))
                await asyncio.sleep(0.1)  # 让 B 阻塞在 general 信号量上

                task_c = asyncio.create_task(run(db_c, "帮我安排明天下午三点的会议", selected_tool="schedule"))
                _, _, events_c = await asyncio.wait_for(task_c, timeout=3.0)

                self.assertTrue(any(item["event"] == "run_completed" for item in events_c))
                self.assertFalse(task_a.done())
                self.assertFalse(task_b.done())

                release_general.set()
                await asyncio.gather(task_a, task_b)
            finally:
                db_a.close()
                db_b.close()
                db_c.close()

    async def test_cancel_while_waiting_for_gate(self) -> None:
        """等待闸门期间用户取消：run 以 run_cancelled 收口，不悬挂。"""
        get_settings().agent_backend = "legacy"
        get_settings().agent_max_concurrent_runs = 1
        get_settings().agent_intake_reserved_slots = 0
        _reset_concurrency_state()

        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_general(db, thread, assistant_message, agent_run, *, user_message, attachment_parts, conversation_history_lines=None, stream_id=None):
            entered.set()
            await release.wait()
            assistant_message.text_content = "好的"
            yield {"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": "好的"}}

        with patch("app.domains.conversation.service.stream_general_chat", side_effect=slow_general), \
             patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat"), \
             patch("app.agent.llm.generate_conversation_title", return_value="标题"):
            db_a = self.session_factory()
            db_b = self.session_factory()
            try:
                thread_a = create_conversation(db_a, self.user.id)
                _, _, _, run_a = queue_message(db_a, self.user.id, thread_a.id, ConversationSendMessageRequest(text_content="问题 A"))
                task_a = asyncio.create_task(self._collect(db_a, thread_a, run_a))
                await entered.wait()  # A 占满闸门

                thread_b = create_conversation(db_b, self.user.id)
                _, _, _, run_b = queue_message(db_b, self.user.id, thread_b.id, ConversationSendMessageRequest(text_content="问题 B"))
                task_b = asyncio.create_task(self._collect(db_b, thread_b, run_b))
                await asyncio.sleep(0.1)  # 让 B 阻塞在闸门上

                self.assertTrue(
                    abort_stream(db_b, user_id=self.user.id, conversation_id=thread_b.id, stream_id=run_b.stream_token)
                )
                events_b = await asyncio.wait_for(task_b, timeout=3.0)
                self.assertEqual([item["event"] for item in events_b], ["run_started", "run_cancelled"])
                self.assertEqual(db_b.get(AgentRun, run_b.id).stream_status, "cancelled")

                release.set()
                await task_a
            finally:
                db_a.close()
                db_b.close()

    async def test_same_conversation_runs_serialize(self) -> None:
        """每会话单 run 串行化：第二个 run 在首个 run 结束前保持 pending 等待。"""
        get_settings().agent_backend = "legacy"
        get_settings().agent_max_concurrent_runs = 0  # 关闸门，隔离每会话串行锁
        _reset_concurrency_state()

        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_general(db, thread, assistant_message, agent_run, *, user_message, attachment_parts, conversation_history_lines=None, stream_id=None):
            entered.set()
            await release.wait()
            assistant_message.text_content = "好的"
            yield {"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": "好的"}}

        with patch("app.domains.conversation.service.stream_general_chat", side_effect=slow_general), \
             patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat"), \
             patch("app.agent.llm.generate_conversation_title", return_value="标题"):
            db_a = self.session_factory()
            db_b = self.session_factory()
            try:
                thread = create_conversation(db_a, self.user.id)
                _, _, _, run_a = queue_message(db_a, self.user.id, thread.id, ConversationSendMessageRequest(text_content="问题 A"))
                _, _, _, run_b = queue_message(db_b, self.user.id, thread.id, ConversationSendMessageRequest(text_content="问题 B"))

                task_a = asyncio.create_task(self._collect(db_a, thread, run_a))
                await entered.wait()  # A 已进入执行主体（持有每会话串行锁）

                task_b = asyncio.create_task(self._collect(db_b, thread, run_b))
                await asyncio.sleep(0.1)
                self.assertFalse(task_b.done())
                # B 尚未认领（仍为 pending），证明其停在每会话串行锁上。
                self.assertEqual(db_b.get(AgentRun, run_b.id).stream_status, "pending")

                release.set()
                events_a = await task_a
                events_b = await asyncio.wait_for(task_b, timeout=3.0)
                self.assertEqual(events_a[-1]["event"], "run_completed")
                self.assertEqual(events_b[-1]["event"], "run_completed")
            finally:
                db_a.close()
                db_b.close()

    async def test_graph_path_smoke_with_gate(self) -> None:
        """回归：默认 graph 后端 + 默认闸门下，简单 general_chat 冒烟不破坏。"""
        get_settings().agent_backend = "langgraph"
        _reset_concurrency_state()

        with patch("app.domains.conversation.stream_runtime.write_user_memory.delay"), \
             patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[]), \
             patch("app.agent.llm.create_chat_model", return_value=_FakeStreamingChatModel(text="好的，我来帮你。")), \
             patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured), \
             patch("app.agent.llm.generate_conversation_title", return_value="冒烟"), \
             patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat"), \
             patch("app.domains.conversation.service.MemoryService.retrieve_context"), \
             patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[]):
            thread = create_conversation(self.db, self.user.id)
            _, _, assistant_message, agent_run = queue_message(self.db, self.user.id, thread.id, ConversationSendMessageRequest(text_content="你好"))
            events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(events[0]["event"], "run_started")
        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertEqual(self.db.get(ConversationMessage, assistant_message.id).text_content, "好的，我来帮你。")
