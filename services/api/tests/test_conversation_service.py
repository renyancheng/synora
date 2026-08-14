import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent import checkpointer as checkpointer_module, llm
from app.agent.graph import build_graph
from app.config import get_settings
from app.db import Base
from app.domains.approval.service import create_approval_request
from app.domains.conversation.agent_service import reflect_step
from app.domains.conversation.service import (
    _claim_pending_stream,
    resume_stream_from_checkpoint,
    abort_stream,
    apply_action,
    consume_stream,
    create_conversation,
    delete_conversation,
    queue_message,
    rewind_last_turn,
    update_conversation_title,
)
from app.domains.conversation.stream_runtime import AgentRunCancelled, is_stream_cancelled, raise_if_stream_cancelled
from app.domains.quick_note.service import delete_note
from app.domains.schedule.service import build_approval_draft_hash, build_draft_hash, delete_schedule
from app.models import AgentRun, ApprovalRequest, Attachment, ConversationMessage, ConversationPendingState, NotificationAudit, QuickNote, ReminderJob, Schedule, User
from app.schemas.common import EventDateTimeValue
from app.schemas.conversation import ConversationActionRequest, ConversationSendMessageRequest
from app.schemas.schedule import ScheduleEventDraft


def _fake_ainvoke_structured(settings, **kwargs):
    """图路径 mock：plan/reflect 的结构化调用按 operation 返回固定结果。"""
    operation = kwargs.get("operation")
    if operation == "agent_plan":
        return llm.PlanResult(plan="回答用户")
    if operation == "agent_reflect":
        return llm.ReflectDecision(is_complete=True, rationale="信息已充分")
    raise AssertionError(f"unexpected ainvoke_structured operation: {operation}")


async def _collect_stream(agen):
    """把 async generator 收集成事件列表（供并发 abort 测试使用）。"""
    return [item async for item in agen]


class _FakeStreamingChatModel:
    """手动 tool-call 循环的流式模型：bind_tools 直通，astream 产 AIMessageChunk。"""

    def __init__(self, text: str = "", tool_calls: list | None = None, capture: list | None = None) -> None:
        self._text = text
        self._tool_calls = tool_calls or []
        self.capture = capture

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        from langchain_core.messages import AIMessageChunk

        if self.capture is not None:
            self.capture.append(messages)
        if self._text:
            yield AIMessageChunk(content=self._text)
        for tc in self._tool_calls:
            yield AIMessageChunk(content="", tool_calls=[tc])


class _ToolThenTextChatModel:
    """两轮模型：第一轮产出工具调用，第二轮产出最终文本；记录 bind_tools 收到的工具。"""

    def __init__(self, tool_call: dict, final_text: str) -> None:
        self._tool_call = tool_call
        self._final_text = final_text
        self._rounds = 0
        self.bound_tools: list | None = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def astream(self, messages):
        from langchain_core.messages import AIMessageChunk

        self._rounds += 1
        if self._rounds == 1:
            yield AIMessageChunk(content="", tool_calls=[self._tool_call])
        else:
            yield AIMessageChunk(content=self._final_text)


class _EmptyThenTextChatModel:
    """前 N 轮返回空流（触发空回答护栏重跑），之后返回正常文本。"""

    def __init__(self, final_text: str, rounds_before_text: int = 1) -> None:
        self._final_text = final_text
        self._rounds_before_text = rounds_before_text
        self._rounds = 0

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        from langchain_core.messages import AIMessageChunk

        self._rounds += 1
        if self._rounds <= self._rounds_before_text:
            return
        yield AIMessageChunk(content=self._final_text)


class _AlwaysEmptyChatModel:
    """永远返回空流：验证空回答兜底文案收口。"""

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        if False:
            yield None
        return


class _ToolCallWithEmptyPlaceholdersModel:
    """复刻 dashscope 兼容模式的真实流：工具调用之后追加空名占位条目。

    第一轮：真实调用 + 两个 name='' 占位条目（不得覆盖真调用）；
    第二轮：输出最终文本。
    """

    def __init__(self, final_text: str) -> None:
        self._final_text = final_text
        self._rounds = 0

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        from langchain_core.messages import AIMessageChunk

        self._rounds += 1
        if self._rounds == 1:
            yield AIMessageChunk(
                content="",
                tool_calls=[
                    {"name": "get_current_time", "args": {}, "id": "call-1", "type": "tool_call"}
                ],
            )
            yield AIMessageChunk(
                content="",
                tool_calls=[{"name": "", "args": {}, "id": "", "type": "tool_call"}],
            )
            yield AIMessageChunk(
                content="",
                tool_calls=[{"name": "", "args": {}, "id": "", "type": "tool_call"}],
            )
        else:
            yield AIMessageChunk(content=self._final_text)


class _WebSearchFragmentedModel:
    """复刻 dashscope 真实流：web_search 调用 name 先到，args 以分片随后下发。"""

    def __init__(self, final_text: str) -> None:
        self._final_text = final_text
        self._rounds = 0

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        from langchain_core.messages import AIMessageChunk

        self._rounds += 1
        if self._rounds == 1:
            yield AIMessageChunk(
                content="",
                tool_calls=[{"name": "web_search", "args": {}, "id": "call-1", "type": "tool_call"}],
                tool_call_chunks=[{"name": "web_search", "args": "", "index": 0, "id": "call-1"}],
            )
            yield AIMessageChunk(
                content="",
                tool_calls=[{"name": "", "args": {}, "id": "", "type": "tool_call"}],
                tool_call_chunks=[{"name": None, "args": '{"query": ', "index": 0, "id": ""}],
            )
            yield AIMessageChunk(
                content="",
                tool_calls=[],
                tool_call_chunks=[{"name": None, "args": '"DeepSeek API', "index": 0, "id": ""}],
            )
            yield AIMessageChunk(
                content="",
                tool_calls=[],
                tool_call_chunks=[{"name": None, "args": ' 价格 2026"}', "index": 0, "id": ""}],
            )
            yield AIMessageChunk(
                content="",
                tool_calls=[{"name": "", "args": {}, "id": "", "type": "tool_call"}],
                tool_call_chunks=[{"name": None, "args": "", "index": 0, "id": ""}],
            )
        else:
            yield AIMessageChunk(content=self._final_text)


class _FakeWebSearchTool:
    """捕获调用参数的联网搜索假工具。"""

    name = "web_search"

    def __init__(self, capture: dict) -> None:
        self._capture = capture

    async def ainvoke(self, args):
        self._capture.update(args)
        return {
            "status": "ok",
            "content": "DeepSeek API 价格：输入 2 元/百万 tokens，输出 8 元/百万 tokens。",
            "references": [{"title": "DeepSeek 定价", "link": "https://api-docs.deepseek.com/pricing"}],
        }


class _FakeTimeTool:
    """模拟 MCP 返回的 get_current_time langchain 工具。"""

    name = "get_current_time"

    async def ainvoke(self, args):
        return {
            "status": "ok",
            "local_time": "2026-08-13 15:30:00",
            "timezone": "Asia/Shanghai",
            "weekday": "周四",
            "utc_time": "2026-08-13 07:30:00",
            "iso": "2026-08-13T07:30:00+00:00",
        }


class ConversationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # 测试强制 sqlite :memory: checkpointer，隔离 dev postgres checkpoint
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
        checkpointer_module.reset_checkpointer()
        build_graph.cache_clear()

    def _draft(self) -> ScheduleEventDraft:
        return ScheduleEventDraft(
            title="教学例会",
            location="学院会议室",
            details="讨论课程安排",
            source_text="明天下午三点开教学例会",
            isAllDay=False,
            start=EventDateTimeValue(dateTime=datetime.fromisoformat("2026-05-24T15:00:00+08:00"), timeZone="Asia/Shanghai"),
            end=EventDateTimeValue(dateTime=datetime.fromisoformat("2026-05-24T16:00:00+08:00"), timeZone="Asia/Shanghai"),
            recurrence=[],
            source_attachment_ids=[],
            parse_confidence=0.92,
            evidence_digest=["明天下午三点", "教学例会"],
        )

    async def test_reflect_receives_execution_evidence_and_continues_when_answer_missing(self) -> None:
        assistant_message = ConversationMessage(
            conversation_id=1,
            role="assistant",
            message_type="text",
            status="streaming",
            text_content="",
            structured_payload_json={},
        )
        self.db.add(assistant_message)
        self.db.commit()
        captured: list[dict] = []

        async def reflect(settings, **kwargs):
            captured.append(kwargs)
            return llm.ReflectDecision(
                is_complete=False,
                rationale="还需要组织答案",
                follow_up_prompt="根据工具结果给出简洁答复",
            )

        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=reflect):
            result = await reflect_step(
                self.db,
                SimpleNamespace(),
                assistant_message,
                SimpleNamespace(),
                state={
                    "user_message": "现在几点了",
                    "plan": "查询当前时间后回答",
                    "iteration_count": 1,
                    "max_iterations": 3,
                    "current_aimessage": {"content": "", "tool_calls": [{"name": "get_current_time"}]},
                    "observation": "get_current_time: 2026-08-13 15:30",
                    "agent_messages": [
                        {"role": "tool", "name": "get_current_time", "content": "2026-08-13 15:30"}
                    ],
                },
                emit=lambda _event: None,
            )

        evidence = json.loads(captured[0]["user_text"])
        self.assertEqual(evidence["plan"], "查询当前时间后回答")
        self.assertEqual(evidence["observation"], "get_current_time: 2026-08-13 15:30")
        self.assertEqual(evidence["tool_messages"][0]["content"], "2026-08-13 15:30")
        self.assertEqual(evidence["current_assistant_output"], "")
        self.assertEqual(result["loop_decision"], "continue")
        self.assertEqual(result["follow_up_prompt"], "根据工具结果给出简洁答复")

    async def test_reflect_does_not_loop_after_tool_failure(self) -> None:
        assistant_message = ConversationMessage(
            conversation_id=1, role="assistant", message_type="text", status="streaming", text_content="", structured_payload_json={}
        )
        self.db.add(assistant_message)
        self.db.commit()
        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock) as reflect_mock:
            result = await reflect_step(
                self.db,
                SimpleNamespace(),
                assistant_message,
                SimpleNamespace(),
                state={
                    "iteration_count": 1,
                    "max_iterations": 3,
                    "current_aimessage": {"content": "", "tool_calls": [{"name": "get_current_time"}]},
                    "tool_failed": True,
                },
                emit=lambda _event: None,
            )

        self.assertEqual(result["loop_decision"], "done")
        self.assertEqual(result["reflection"], "工具执行失败，避免重复调用")
        reflect_mock.assert_not_awaited()

    async def test_reflect_closes_after_successful_tool_and_user_answer(self) -> None:
        assistant_message = ConversationMessage(
            conversation_id=1, role="assistant", message_type="text", status="streaming", text_content="现在是周四 15:30。", structured_payload_json={}
        )
        self.db.add(assistant_message)
        self.db.commit()
        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock) as reflect_mock:
            result = await reflect_step(
                self.db,
                SimpleNamespace(),
                assistant_message,
                SimpleNamespace(),
                state={
                    "iteration_count": 1,
                    "max_iterations": 3,
                    "current_aimessage": {
                        "content": "现在是周四 15:30。",
                        "tool_calls": [{"name": "get_current_time"}],
                    },
                    "observation": "get_current_time: 2026-08-13 15:30",
                },
                emit=lambda _event: None,
            )

        self.assertEqual(result["loop_decision"], "done")
        self.assertEqual(result["reflection"], "工具已返回且已生成用户可读回答")
        reflect_mock.assert_not_awaited()

    async def test_pending_stream_can_only_be_claimed_once_across_sessions(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="测试并发消费"),
        )
        first_consumer = self.session_factory()
        second_consumer = self.session_factory()
        try:
            # 两个消费者都先读到 pending，模拟它们在旧实现中会同时进入执行路径。
            self.assertEqual(first_consumer.get(AgentRun, agent_run.id).stream_status, "pending")
            self.assertEqual(second_consumer.get(AgentRun, agent_run.id).stream_status, "pending")

            self.assertTrue(_claim_pending_stream(first_consumer, agent_run.id))
            self.assertFalse(_claim_pending_stream(second_consumer, agent_run.id))
            self.db.expire_all()
            self.assertEqual(self.db.get(AgentRun, agent_run.id).stream_status, "active")
        finally:
            first_consumer.close()
            second_consumer.close()

    async def test_abort_persists_cancelling_state_for_another_worker(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="测试跨实例取消"),
        )
        stream_worker = self.session_factory()
        abort_worker = self.session_factory()
        try:
            self.assertTrue(_claim_pending_stream(stream_worker, agent_run.id))
            self.assertTrue(
                abort_stream(
                    abort_worker,
                    user_id=self.user.id,
                    conversation_id=thread.id,
                    stream_id=agent_run.stream_token,
                )
            )
            # stream_worker 在 abort 前已读取过 run；检查必须重新查库才能看到取消请求。
            with self.assertRaises(AgentRunCancelled):
                raise_if_stream_cancelled(
                    stream_worker,
                    agent_run.stream_token,
                    force_database_check=True,
                )
            self.db.expire_all()
            self.assertEqual(self.db.get(AgentRun, agent_run.id).stream_status, "cancelling")
        finally:
            stream_worker.close()
            abort_worker.close()

    async def test_abort_does_not_cancel_a_stream_from_another_conversation(self) -> None:
        first_thread = create_conversation(self.db, self.user.id)
        second_thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            second_thread.id,
            ConversationSendMessageRequest(text_content="隔离取消请求"),
        )

        self.assertFalse(
            abort_stream(
                self.db,
                user_id=self.user.id,
                conversation_id=first_thread.id,
                stream_id=agent_run.stream_token,
            )
        )
        self.db.expire_all()
        self.assertEqual(self.db.get(AgentRun, agent_run.id).stream_status, "pending")

    async def test_cancelling_stream_is_closed_without_starting_agent(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="取消先于消费启动"),
        )
        self.assertTrue(
            abort_stream(
                self.db,
                user_id=self.user.id,
                conversation_id=thread.id,
                stream_id=agent_run.stream_token,
            )
        )

        events = [
            item
            async for item in consume_stream(
                self.db,
                self.user.id,
                thread.id,
                agent_run.stream_token,
            )
        ]

        self.assertEqual([item["event"] for item in events], ["run_cancelled"])
        self.db.expire_all()
        self.assertEqual(self.db.get(AgentRun, agent_run.id).stream_status, "cancelled")
        self.assertEqual(self.db.get(ConversationMessage, assistant_message.id).status, "completed")

    async def test_resume_rejects_active_run_and_marks_it_failed(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="恢复中断流"),
        )
        self.assertTrue(_claim_pending_stream(self.db, agent_run.id))
        agent_run.checkpoint_thread_id = f"agent_run_{agent_run.id}"
        self.db.commit()

        with self.assertRaisesRegex(ValueError, "已安全终止"):
            _ = [
                item
                async for item in resume_stream_from_checkpoint(
                    self.db, self.user.id, thread.id, agent_run.stream_token
                )
            ]

        self.db.expire_all()
        refreshed = self.db.get(AgentRun, agent_run.id)
        self.assertEqual(refreshed.stream_status, "failed")
        self.assertIn("可能重复执行工具", refreshed.error_message or "")

    async def test_resume_rejects_failed_checkpoint_before_side_effects(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="拒绝不安全恢复"),
        )
        agent_run.stream_status = "failed"
        agent_run.status = "failed"
        agent_run.checkpoint_thread_id = f"agent_run_{agent_run.id}"
        self.db.commit()

        with patch("app.agent.checkpointer.setup_checkpointer", new_callable=AsyncMock), patch(
            "app.agent.graph.build_graph",
            return_value=SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(next=("observe",)))),
        ):
            with self.assertRaisesRegex(ValueError, "已拒绝恢复"):
                _ = [
                    item
                    async for item in resume_stream_from_checkpoint(
                        self.db, self.user.id, thread.id, agent_run.stream_token
                    )
                ]

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model", return_value=_FakeStreamingChatModel(text="好的，我来帮你一起整理。"))
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学安排")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_send_general_chat_message(
        self,
        _extract_mock,
        memory_mock,
        _intent_mock,
        _title_mock,
        _ainvoke_mock,
        _chat_model_mock,
        _tools_mock,
        write_memory_mock,
    ) -> None:
        memory_mock.return_value = SimpleNamespace(
            summary="韩老师通常希望提前一天提醒。",
            items=[{"title": "提醒偏好", "content": "通常提前一天提醒"}],
        )
        thread = create_conversation(self.db, self.user.id)
        _, user_message, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好，帮我看看今天安排"),
        )

        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(user_message.text_content, "你好，帮我看看今天安排")
        self.assertEqual(events[0]["event"], "run_started")
        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertEqual(self.db.get(type(thread), thread.id).title, "教学安排")
        self.assertEqual(self.db.get(type(assistant_message), assistant_message.id).text_content, "好的，我来帮你一起整理。")
        self.assertEqual(write_memory_mock.call_count, 0)
        # 图路径全量显式化：plan→act→observe→reflect 四步落库为 reasoning_step 卡片
        step_snapshots = [
            item["data"]["message"]
            for item in events
            if item["event"] == "card_snapshot" and item["data"]["message"]["message_type"] == "reasoning_step"
        ]
        self.assertEqual(len(step_snapshots), 1)
        steps = step_snapshots[0]["structured_payload"]["steps"]
        self.assertEqual([s["step_type"] for s in steps], ["plan", "act", "observe", "reflect"])
        self.assertTrue(all(s["status"] == "completed" for s in steps))

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock)
    @patch(
        "app.agent.llm.create_chat_model",
        return_value=_ToolCallWithEmptyPlaceholdersModel(final_text="现在是周四 15:30。"),
    )
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="时间问答")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_tool_call_survives_empty_name_placeholder_chunks(
        self,
        _extract_mock,
        memory_mock,
        _intent_mock,
        _title_mock,
        _ainvoke_mock,
        _chat_model_mock,
        tools_mock,
        _write_memory_mock,
    ) -> None:
        """dashscope 兼容模式会在真实工具调用后追加 name='' 占位条目，
        工具调用不能被占位条目覆盖，否则 observe 空转并落到空回答兜底。"""
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        tools_mock.return_value = [_FakeTimeTool()]
        reflect_calls = {"n": 0}

        async def _reflect_side_effect(settings, **kwargs):
            if kwargs.get("operation") == "agent_plan":
                return llm.PlanResult(plan="查询当前时间后回答")
            if kwargs.get("operation") == "agent_reflect":
                reflect_calls["n"] += 1
                if reflect_calls["n"] == 1:
                    return llm.ReflectDecision(
                        is_complete=False,
                        rationale="需要时间信息",
                        follow_up_prompt="结合工具返回的时间给出回答",
                    )
                return llm.ReflectDecision(is_complete=True, rationale="信息已充分")
            raise AssertionError(f"unexpected operation: {kwargs.get('operation')}")

        _ainvoke_mock.side_effect = _reflect_side_effect

        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="现在几点了"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertTrue(
            any(
                item["event"] == "tool_call_completed" and item["data"]["tool_name"] == "get_current_time"
                for item in events
            )
        )
        self.assertEqual(
            self.db.get(type(assistant_message), assistant_message.id).text_content,
            "现在是周四 15:30。",
        )
        self.assertEqual(events[-1]["event"], "run_completed")

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock)
    @patch(
        "app.agent.llm.create_chat_model",
        return_value=_WebSearchFragmentedModel(final_text="根据搜索结果，DeepSeek API 输入价格 2 元/百万 tokens。"),
    )
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="搜索问答")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_web_search_args_fragments_are_reassembled_and_invoked(
        self,
        _extract_mock,
        memory_mock,
        _intent_mock,
        _title_mock,
        _ainvoke_mock,
        _chat_model_mock,
        tools_mock,
        _write_memory_mock,
    ) -> None:
        """dashscope 把 web_search 的 args 以 JSON 分片下发：分片必须拼回完整
        query 后调用工具，否则参数校验失败并退化到空回答兜底。"""
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        captured: dict = {}
        tools_mock.return_value = [_FakeWebSearchTool(captured)]

        async def _reflect_side_effect(settings, **kwargs):
            if kwargs.get("operation") == "agent_plan":
                return llm.PlanResult(plan="联网搜索 DeepSeek API 价格")
            if kwargs.get("operation") == "agent_reflect":
                return llm.ReflectDecision(
                    is_complete=False,
                    rationale="需要搜索结果",
                    follow_up_prompt="结合搜索结果给出回答",
                )
            raise AssertionError(f"unexpected operation: {kwargs.get('operation')}")

        _ainvoke_mock.side_effect = _reflect_side_effect

        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="帮我搜一下 deepseek 现在的 api 价格"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(captured["query"], "DeepSeek API 价格 2026")
        self.assertTrue(
            any(
                item["event"] == "tool_call_completed" and item["data"]["tool_name"] == "web_search"
                for item in events
            )
        )
        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertIn(
            "2 元/百万",
            self.db.get(type(assistant_message), assistant_message.id).text_content or "",
        )

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model", return_value=_EmptyThenTextChatModel("你好！我是 Synora。", rounds_before_text=2))
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured)
    @patch("app.agent.llm.generate_conversation_title", return_value="打招呼")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_empty_first_answer_triggers_rerun_and_completes_with_text(
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
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="hi"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        # 前两轮空流被空回答护栏拦截并重跑，最终以非空文本完成。
        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertEqual(self.db.get(type(assistant_message), assistant_message.id).text_content, "你好！我是 Synora。")
        step_snapshots = [
            item["data"]["message"]
            for item in events
            if item["event"] == "card_snapshot" and item["data"]["message"]["message_type"] == "reasoning_step"
        ]
        steps = step_snapshots[0]["structured_payload"]["steps"]
        self.assertEqual([s["step_type"] for s in steps].count("act"), 3)
        self.assertTrue(
            any("未生成任何回答文本" in str(s.get("content")) for s in steps if s["step_type"] == "reflect")
        )

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model", return_value=_AlwaysEmptyChatModel())
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured)
    @patch("app.agent.llm.generate_conversation_title", return_value="空回答兜底")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_always_empty_answer_falls_back_to_explicit_text(
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
        from app.domains.conversation.service import EMPTY_ANSWER_FALLBACK_TEXT

        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="what time is it now"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        # 重试用尽后以明确兜底文案收口：绝不出现空回答气泡。
        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertEqual(
            self.db.get(type(assistant_message), assistant_message.id).text_content,
            EMPTY_ANSWER_FALLBACK_TEXT,
        )
        refreshed = self.db.get(AgentRun, agent_run.id)
        self.assertEqual(refreshed.output_json.get("completion_status"), "degraded")
        self.assertEqual(
            [item["operation"] for item in refreshed.output_json.get("degradations") or []][-1],
            "empty_answer_fallback",
        )

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model", return_value=_FakeStreamingChatModel(text="好的，我来帮你一起整理。"))
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学安排")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_rewind_after_graph_run_removes_reasoning_step_message(
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
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好，帮我看看今天安排"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertIsNotNone(
            self.db.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == thread.id,
                    ConversationMessage.message_type == "reasoning_step",
                )
            )
        )

        rewind_last_turn(self.db, self.user.id, thread.id)

        # 撤回后本轮 reasoning_step 消息随 output_json.created_message_ids 一并删除。
        self.assertIsNone(
            self.db.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == thread.id,
                    ConversationMessage.message_type == "reasoning_step",
                )
            )
        )
        remaining = self.db.scalars(
            select(ConversationMessage).where(ConversationMessage.conversation_id == thread.id)
        ).all()
        self.assertEqual(remaining, [])

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model", return_value=_FakeStreamingChatModel(text="好的，我来帮你整理。"))
    @patch("app.agent.llm.generate_conversation_title", return_value="降级问答")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_plan_llm_failure_completes_run_as_degraded(
        self,
        _extract_mock,
        memory_mock,
        _intent_mock,
        _title_mock,
        _chat_model_mock,
        _tools_mock,
        _write_memory_mock,
    ) -> None:
        memory_mock.return_value = SimpleNamespace(summary="", items=[])

        async def _plan_fails_reflect_ok(settings, **kwargs):
            if kwargs.get("operation") == "agent_plan":
                raise RuntimeError("plan llm down")
            if kwargs.get("operation") == "agent_reflect":
                return llm.ReflectDecision(is_complete=True, rationale="信息已充分")
            raise AssertionError(f"unexpected operation: {kwargs.get('operation')}")

        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="帮我看看这周的工作安排，顺便整理一下下周的出差计划"),
        )
        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_plan_fails_reflect_ok):
            events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(events[-1]["event"], "run_completed")
        refreshed = self.db.get(AgentRun, agent_run.id)
        self.assertEqual(refreshed.status, "completed")
        self.assertEqual(refreshed.output_json.get("completion_status"), "degraded")
        self.assertEqual([item["operation"] for item in refreshed.output_json.get("degradations") or []], ["plan"])
        # 降级步骤在推理轨迹卡中可见，供前端明确区分降级完成。
        step_snapshots = [
            item["data"]["message"]
            for item in events
            if item["event"] == "card_snapshot" and item["data"]["message"]["message_type"] == "reasoning_step"
        ]
        steps = step_snapshots[0]["structured_payload"]["steps"]
        self.assertTrue(steps[0].get("degraded"))
        self.assertEqual(steps[0]["plan_source"], "llm")

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model", return_value=_FakeStreamingChatModel(text="现在是 15:30。"))
    @patch("app.agent.llm.generate_conversation_title", return_value="时间问答")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_simple_question_skips_plan_llm_call(
        self,
        _extract_mock,
        memory_mock,
        _intent_mock,
        _title_mock,
        _chat_model_mock,
        _tools_mock,
        _write_memory_mock,
    ) -> None:
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        plan_calls = {"n": 0}

        async def _no_plan(settings, **kwargs):
            if kwargs.get("operation") == "agent_plan":
                plan_calls["n"] += 1
                raise AssertionError("简单问答不应调用 plan LLM")
            if kwargs.get("operation") == "agent_reflect":
                return llm.ReflectDecision(is_complete=True, rationale="信息已充分")
            raise AssertionError(f"unexpected operation: {kwargs.get('operation')}")

        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="现在几点了"),
        )
        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_no_plan):
            events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertEqual(plan_calls["n"], 0)
        # 确定性 plan 仍进入 act 上下文并展示为推理步骤，但不是装饰性的伪计划。
        step_snapshots = [
            item["data"]["message"]
            for item in events
            if item["event"] == "card_snapshot" and item["data"]["message"]["message_type"] == "reasoning_step"
        ]
        plan_steps = [s for s in step_snapshots[0]["structured_payload"]["steps"] if s["step_type"] == "plan"]
        self.assertEqual(plan_steps[0]["plan_source"], "deterministic")
        self.assertEqual(plan_steps[0]["content"], "回答用户问题")

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model")
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured)
    @patch("app.agent.llm.generate_conversation_title", return_value="历史问答")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.ConversationHistorySearchService.retrieve_history_lines")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_general_chat_includes_semantic_conversation_history_lines(
        self,
        _extract_mock,
        history_mock,
        memory_mock,
        _intent_mock,
        _title_mock,
        _ainvoke_mock,
        chat_model_mock,
        _tools_mock,
        _write_memory_mock,
    ) -> None:
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        history_mock.return_value = [
            "用户：上周已经确定答辩地点在信息楼 202",
            "助手：你之前提到答辩安排在周三下午",
        ]
        captured: list[list] = []
        chat_model_mock.return_value = _FakeStreamingChatModel(text="我已经结合历史上下文整理好了。", capture=captured)
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="那地点还是之前那个吗"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertTrue(captured)
        messages = captured[0]
        final_prompt = llm.extract_message_text(messages[-1])
        self.assertIn("同一会话较早相关历史：", final_prompt)
        self.assertIn("信息楼 202", final_prompt)
        self.assertIn("当前输入：\n那地点还是之前那个吗", final_prompt)

    async def test_stream_returns_run_failed_when_llm_not_configured(self) -> None:
        with patch.object(get_settings(), "llm_api_key", ""):
            thread = create_conversation(self.db, self.user.id)
            _, _, assistant_message, agent_run = queue_message(
                self.db,
                self.user.id,
                thread.id,
                ConversationSendMessageRequest(text_content="你好，帮我看看今天安排"),
            )

            events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(events[0]["event"], "run_started")
        self.assertEqual(events[-1]["event"], "run_failed")
        self.assertEqual(events[-1]["data"]["code"], "llm_not_configured")
        self.assertFalse(events[-1]["data"]["retryable"])
        refreshed = self.db.get(type(assistant_message), assistant_message.id)
        self.assertEqual(refreshed.status, "failed")
        self.assertEqual(refreshed.text_content, "")
        self.assertEqual(self.db.get(AgentRun, agent_run.id).output_json.get("completion_status"), "failed")

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model", return_value=_FakeStreamingChatModel())
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured)
    @patch("app.agent.llm.generate_conversation_title", return_value="测试聊天")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_general_chat_does_not_leak_langchain_internal_repr(
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
        from app.domains.conversation.service import EMPTY_ANSWER_FALLBACK_TEXT

        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好"),
        )

        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(events[-1]["event"], "run_completed")
        # 空模型输出不泄露 langchain 内部表示，而是空回答兜底文案收口。
        self.assertEqual(
            self.db.get(type(assistant_message), assistant_message.id).text_content,
            EMPTY_ANSWER_FALLBACK_TEXT,
        )

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学例会")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    @patch("app.domains.conversation.service.ConversationHistorySearchService.retrieve_history_lines")
    async def test_schedule_message_creates_pending_cards(
        self,
        history_mock,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        history_mock.return_value = []
        draft = self._draft()
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["明天下午三点"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)

        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        card_events = [item for item in events if item["event"] == "card_snapshot"]
        self.assertEqual([item["data"]["message"]["message_type"] for item in card_events], ["schedule_draft_card", "conflict_card", "reasoning_step"])
        self.assertTrue(any(item["event"] == "approval_required" for item in events))
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNotNone(pending)
        self.assertEqual(pending.stage, "approval_pending")

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学例会")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    @patch("app.domains.conversation.service.ConversationHistorySearchService.retrieve_history_lines")
    async def test_schedule_intake_passes_semantic_history_lines_to_tool_context(
        self,
        history_mock,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        history_mock.return_value = ["用户：之前说过地点在学院会议室"]
        draft = self._draft()
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["学院会议室"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)

        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="那就按之前说的地点安排答辩", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        first_tool_payload = invoke_tool_mock.await_args_list[0].args[1]
        self.assertEqual(first_tool_payload["context"]["conversation_history_lines"], ["用户：之前说过地点在学院会议室"])

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学例会")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    @patch("app.domains.conversation.service._build_conversation_history_recall")
    @patch("app.domains.conversation.service.ConversationHistorySearchService.retrieve_history_lines")
    async def test_schedule_intake_falls_back_to_lexical_history_recall(
        self,
        history_mock,
        fallback_mock,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        history_mock.return_value = []
        fallback_mock.return_value = ["用户：之前说过时间是周三下午"]
        draft = self._draft()
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["周三下午"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)

        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="那就还是之前那个时间", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        first_tool_payload = invoke_tool_mock.await_args_list[0].args[1]
        self.assertEqual(first_tool_payload["context"]["conversation_history_lines"], ["用户：之前说过时间是周三下午"])

    @patch("app.domains.conversation.service.ConversationHistorySearchService.upsert_message")
    def test_queue_message_upserts_user_text_to_history_index(self, upsert_mock) -> None:
        thread = create_conversation(self.db, self.user.id)

        _, user_message, _, _ = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="帮我记录一下下周答辩安排"),
        )

        upsert_mock.assert_called_once()
        self.assertEqual(upsert_mock.call_args.args[0].id, user_message.id)

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[])
    @patch("app.agent.llm.create_chat_model", return_value=_FakeStreamingChatModel(text="好的，我来帮你一起整理。"))
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学安排")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.ConversationHistorySearchService.upsert_message")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_finalize_run_upserts_assistant_text_to_history_index(
        self,
        _extract_mock,
        upsert_mock,
        memory_mock,
        _intent_mock,
        _title_mock,
        _ainvoke_mock,
        _chat_model_mock,
        _tools_mock,
        _write_memory_mock,
    ) -> None:
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好，帮我看看今天安排"),
        )

        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        upserted_ids = [call.args[0].id for call in upsert_mock.call_args_list]
        self.assertIn(assistant_message.id, upserted_ids)

    @patch("app.domains.conversation.service.ConversationHistorySearchService.delete_messages")
    def test_delete_conversation_deletes_history_index_messages(self, delete_mock) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, user_message, assistant_message, _ = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="需要删除的对话"),
        )

        delete_conversation(self.db, self.user.id, thread.id)

        delete_mock.assert_called_once()
        self.assertEqual(delete_mock.call_args.kwargs["conversation_id"], thread.id)
        self.assertCountEqual(delete_mock.call_args.kwargs["message_ids"], [user_message.id, assistant_message.id])

    @patch("app.domains.conversation.service.ConversationHistorySearchService.delete_messages")
    def test_rewind_last_turn_deletes_history_index_messages(self, delete_mock) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, user_message, assistant_message, _ = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="这轮消息待撤回"),
        )

        rewind_last_turn(self.db, self.user.id, thread.id)

        delete_mock.assert_called_once()
        self.assertEqual(delete_mock.call_args.kwargs["conversation_id"], thread.id)
        self.assertCountEqual(delete_mock.call_args.kwargs["message_ids"], [user_message.id, assistant_message.id])

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.pending_service.create_schedule_after_approval")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学例会")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_confirm_schedule_action_updates_existing_cards_in_place(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        create_after_mock,
        write_memory_mock,
    ) -> None:
        draft = self._draft()
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["明天下午三点"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash",
                    },
                },
            ),
        ]
        create_after_mock.return_value = (
            SimpleNamespace(
                id=10,
                title="教学例会",
                details="讨论课程安排",
                source_text="明天下午三点在学院会议室开教学例会",
                start_at=datetime.fromisoformat("2026-05-24T07:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-05-24T08:00:00+00:00"),
                time_zone="Asia/Shanghai",
            ),
            [SimpleNamespace(channel="system")],
        )
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        _, assistant_messages = apply_action(
            self.db,
            self.user.id,
            thread.id,
            ConversationActionRequest(action="confirm_schedule_draft"),
        )

        self.assertEqual(assistant_messages, [])
        cards = self.db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == thread.id, ConversationMessage.action_group_id.is_not(None))
        ).all()
        self.assertTrue(cards)
        self.assertTrue(all((item.structured_payload_json or {}).get("lifecycle_status") == "confirmed" for item in cards))
        self.assertTrue(all((item.structured_payload_json or {}).get("is_actionable") is False for item in cards))
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNone(pending)
        self.assertGreaterEqual(write_memory_mock.call_count, 1)

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学例会")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_confirm_schedule_action_uses_real_schedule_creation_path(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        write_memory_mock,
    ) -> None:
        draft = self._draft()
        draft_hash = build_approval_draft_hash(draft)
        approval, approval_token = create_approval_request(
            self.db,
            user_id=self.user.id,
            action="create_schedule",
            payload={
                "draft": draft.model_dump(mode="json", by_alias=True),
                "conflicts": [],
            },
            draft_hash=draft_hash,
            normalized_payload=draft.model_dump(mode="json", by_alias=True),
            evidence_digest=draft.evidence_digest,
            approval_scope=f"schedule:create:{draft_hash}",
        )
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": draft_hash,
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": list(draft.evidence_digest),
                    "parse_confidence": draft.parse_confidence,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": approval_token,
                        "action": "create_schedule",
                        "expires_at": approval.expires_at.isoformat(),
                        "draft_hash": draft_hash,
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        _, assistant_messages = apply_action(
            self.db,
            self.user.id,
            thread.id,
            ConversationActionRequest(action="confirm_schedule_draft"),
        )

        self.assertEqual(assistant_messages, [])
        schedule = self.db.scalar(select(Schedule).where(Schedule.user_id == self.user.id))
        self.assertIsNotNone(schedule)
        jobs = self.db.scalars(select(ReminderJob).where(ReminderJob.schedule_id == schedule.id).order_by(ReminderJob.id.asc())).all()
        self.assertGreaterEqual(len(jobs), 1)
        self.assertEqual(jobs[0].channel, "system")
        refreshed_approval = self.db.get(ApprovalRequest, approval.id)
        self.assertIsNotNone(refreshed_approval)
        self.assertEqual(refreshed_approval.status, "confirmed")
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNone(pending)
        self.assertGreaterEqual(write_memory_mock.call_count, 1)

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学例会")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_confirm_schedule_action_accepts_non_default_reminder_preset(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        write_memory_mock,
    ) -> None:
        draft = self._draft()
        approval_hash = build_approval_draft_hash(draft)
        approval, approval_token = create_approval_request(
            self.db,
            user_id=self.user.id,
            action="create_schedule",
            payload={
                "draft": draft.model_dump(mode="json", by_alias=True),
                "conflicts": [],
            },
            draft_hash=approval_hash,
            normalized_payload=draft.model_dump(mode="json", by_alias=True),
            evidence_digest=draft.evidence_digest,
            approval_scope=f"schedule:create:{approval_hash}",
        )
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": build_draft_hash(draft),
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": list(draft.evidence_digest),
                    "parse_confidence": draft.parse_confidence,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": approval_token,
                        "action": "create_schedule",
                        "expires_at": approval.expires_at.isoformat(),
                        "draft_hash": approval_hash,
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        _, assistant_messages = apply_action(
            self.db,
            self.user.id,
            thread.id,
            ConversationActionRequest(
                action="confirm_schedule_draft",
                payload={"reminder_preset": "30m_before"},
            ),
        )

        self.assertEqual(assistant_messages, [])
        schedule = self.db.scalar(select(Schedule).where(Schedule.user_id == self.user.id))
        self.assertIsNotNone(schedule)
        self.assertEqual(schedule.reminder_preset, "30m_before")
        self.assertLessEqual(abs(int((schedule.start_at - schedule.reminder_at).total_seconds() // 60)), 30)
        refreshed_approval = self.db.get(ApprovalRequest, approval.id)
        self.assertEqual(refreshed_approval.status, "confirmed")
        self.assertGreaterEqual(write_memory_mock.call_count, 1)

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.pending_service.create_schedule_after_approval")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学例会")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_confirm_schedule_action_succeeds_when_card_finalize_fails(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        create_after_mock,
        write_memory_mock,
    ) -> None:
        draft = self._draft()
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["明天下午三点"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash",
                    },
                },
            ),
        ]
        create_after_mock.return_value = (
            SimpleNamespace(
                id=11,
                title="教学例会",
                details="讨论课程安排",
                source_text="明天下午三点在学院会议室开教学例会",
                start_at=datetime.fromisoformat("2026-05-24T07:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-05-24T08:00:00+00:00"),
                time_zone="Asia/Shanghai",
                reminder_preset="previous_day_1700",
            ),
            [SimpleNamespace(channel="system")],
        )
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        with patch("app.domains.conversation.pending_service.mark_action_group_status", side_effect=RuntimeError("card finalize failed")):
            _, assistant_messages = apply_action(
                self.db,
                self.user.id,
                thread.id,
                ConversationActionRequest(action="confirm_schedule_draft"),
            )

        self.assertEqual(assistant_messages, [])
        self.assertGreaterEqual(write_memory_mock.call_count, 1)
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNotNone(pending)

    @patch("app.domains.conversation.pending_service.write_user_memory.delay")
    @patch("app.domains.conversation.pending_service.save_note_after_approval")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="实验记录")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="quick_note_intake")
    async def test_quick_note_message_and_confirm(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        save_note_mock,
        write_memory_mock,
    ) -> None:
        invoke_tool_mock.return_value = (
            SimpleNamespace(content="note"),
            {
                "status": "pending_approval",
                "normalized_content": "下周整理论文实验记录",
                "preview_tags": ["科研", "待办"],
                "attachment_ids": [],
                "evidence_digest": ["论文", "实验记录"],
                "approval": {
                    "approval_token": "quick-note-token",
                    "action": "create_quick_note",
                    "expires_at": datetime.now(timezone.utc).isoformat(),
                    "draft_hash": "quick-note-hash",
                },
            },
        )
        save_note_mock.return_value = SimpleNamespace(
            id=7,
            content="下周整理论文实验记录",
            topic_tags_json=["科研", "待办"],
        )
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="记一下：下周整理论文实验记录", selected_tool="quick_note"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]
        card_events = [item for item in events if item["event"] == "card_snapshot"]

        self.assertEqual([item["data"]["message"]["message_type"] for item in card_events], ["quick_note_preview_card", "reasoning_step"])

        _, confirm_messages = apply_action(
            self.db,
            self.user.id,
            thread.id,
            ConversationActionRequest(action="confirm_quick_note"),
        )
        self.assertEqual(confirm_messages, [])
        cards = self.db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == thread.id, ConversationMessage.message_type == "quick_note_preview_card")
        ).all()
        self.assertTrue(cards)
        self.assertTrue(all((item.structured_payload_json or {}).get("lifecycle_status") == "confirmed" for item in cards))
        self.assertGreaterEqual(write_memory_mock.call_count, 1)

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="实验记录")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="quick_note_intake")
    async def test_pending_quick_note_regenerates_new_revision(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="note-1"),
                {
                    "status": "pending_approval",
                    "normalized_content": "下周整理论文实验记录",
                    "preview_tags": ["科研", "待办"],
                    "attachment_ids": [],
                    "evidence_digest": ["论文", "实验记录"],
                    "approval": {
                        "approval_token": "quick-note-token-1",
                        "action": "create_quick_note",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "quick-note-hash-1",
                    },
                },
            ),
            (
                SimpleNamespace(content="note-2"),
                {
                    "status": "pending_approval",
                    "normalized_content": "下周三整理论文实验记录并补充图表",
                    "preview_tags": ["科研", "待办", "图表"],
                    "attachment_ids": [],
                    "evidence_digest": ["下周三", "图表"],
                    "approval": {
                        "approval_token": "quick-note-token-2",
                        "action": "create_quick_note",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "quick-note-hash-2",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, _, first_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="记一下：下周整理论文实验记录", selected_tool="quick_note"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, first_run.stream_token)]

        _, _, _, second_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="改成下周三，并补充图表"),
        )
        second_events = [item async for item in consume_stream(self.db, self.user.id, thread.id, second_run.stream_token)]

        cards = [item["data"]["message"] for item in second_events if item["event"] == "card_snapshot"]
        self.assertEqual([item["message_type"] for item in cards], ["quick_note_preview_card", "reasoning_step"])
        self.assertEqual(cards[0]["revision"], 2)
        second_tool_call = invoke_tool_mock.await_args_list[1].args[1]
        self.assertEqual(second_tool_call["content"], "改成下周三，并补充图表")
        self.assertEqual(second_tool_call["context"]["previous_note_content"], "下周整理论文实验记录")
        self.assertEqual(second_tool_call["context"]["latest_user_text"], "改成下周三，并补充图表")
        self.assertEqual(second_tool_call["context"]["pending_regeneration"], "quick_note")
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertEqual(pending.pending_type, "quick_note")
        self.assertEqual(int(pending.meta_json.get("revision") or 0), 2)
        history = self.db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == thread.id)
            .order_by(ConversationMessage.id.asc())
        ).all()
        old_cards = [item for item in history if item.message_type == "quick_note_preview_card" and item.revision == 1]
        self.assertTrue(old_cards)
        self.assertTrue(all((item.structured_payload_json or {}).get("lifecycle_status") == "superseded" for item in old_cards))

    def test_delete_schedule_cascades_reminders_and_audits(self) -> None:
        schedule = Schedule(
            user_id=self.user.id,
            title="测试日程",
            location="会议室",
            details="测试",
            source_text="测试",
            start_at=datetime.now(timezone.utc),
            end_at=datetime.now(timezone.utc),
            time_zone="Asia/Shanghai",
            is_all_day=False,
            recurrence_rules_json=[],
            reminder_offsets_minutes_json=[-30],
            source_attachment_ids=[],
            parse_confidence=0.8,
            scheduled_at=datetime.now(timezone.utc),
            duration_minutes=60,
            reminder_at=datetime.now(timezone.utc),
            source_type="text",
        )
        self.db.add(schedule)
        self.db.commit()
        self.db.refresh(schedule)

        reminder = ReminderJob(
            schedule_id=schedule.id,
            channel="system",
            scheduled_for=datetime.now(timezone.utc),
            status="pending",
        )
        self.db.add(reminder)
        self.db.commit()
        self.db.refresh(reminder)

        audit = NotificationAudit(
            user_id=self.user.id,
            reminder_job_id=reminder.id,
            channel="system",
            recipient="system",
            subject="提醒",
            payload_json="{}",
            status="queued",
            provider="system",
        )
        self.db.add(audit)
        self.db.commit()

        delete_schedule(self.db, self.user.id, schedule.id)

        self.assertIsNone(self.db.get(Schedule, schedule.id))
        self.assertIsNone(self.db.get(ReminderJob, reminder.id))
        self.assertIsNone(self.db.get(NotificationAudit, audit.id))


    def test_queue_message_stores_user_message_metadata(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        attachment = Attachment(
            user_id=self.user.id,
            file_name="agenda.pdf",
            content_type="application/pdf",
            source_type="attachment",
            object_key="attachments/agenda.pdf",
            storage_bucket="synora",
            size_bytes=2048,
            status="uploaded",
        )
        self.db.add(attachment)
        self.db.commit()
        self.db.refresh(attachment)

        _, user_message, _, _ = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(
                text_content="请帮我处理这个附件",
                attachment_ids=[attachment.id],
                selected_tool="schedule",
            ),
        )

        payload = dict(user_message.structured_payload_json or {})
        self.assertEqual(payload["selected_tool"], "schedule")
        self.assertEqual(len(payload["attachment_refs"]), 1)
        self.assertEqual(payload["attachment_refs"][0]["attachment_id"], attachment.id)
        self.assertEqual(payload["attachment_refs"][0]["file_name"], "agenda.pdf")

    def test_update_conversation_title(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        updated = update_conversation_title(self.db, self.user.id, thread.id, "新的标题")
        self.assertEqual(updated.title, "新的标题")

    def test_delete_conversation_removes_runs_and_messages(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好"),
        )
        delete_conversation(self.db, self.user.id, thread.id)
        self.assertIsNone(self.db.get(type(thread), thread.id))
        self.assertIsNone(self.db.get(type(agent_run), agent_run.id))

    def test_delete_conversation_removes_pending_and_related_approvals(self) -> None:
        from app.domains.approval.service import create_approval_request

        thread = create_conversation(self.db, self.user.id)
        user_message = ConversationMessage(
            conversation_id=thread.id,
            role="user",
            message_type="text",
            status="sent",
            text_content="帮我记一下下周汇报",
            structured_payload_json={},
            action_group_id="group-1",
            revision=1,
        )
        self.db.add(user_message)
        approval, token = create_approval_request(
            self.db,
            user_id=self.user.id,
            action="create_quick_note",
            payload={"content": "下周汇报"},
            draft_hash="draft-quick-note",
            normalized_payload={"content": "下周汇报"},
            evidence_digest=[],
            approval_scope="conversation_quick_note:group-1",
        )
        self.db.add(
            ConversationPendingState(
                conversation_id=thread.id,
                user_id=self.user.id,
                pending_type="quick_note",
                stage="approval_pending",
                draft_hash="draft-quick-note",
                approval_token=token,
                source_type="mixed",
                attachment_ids_json=[],
                payload_json={"content": "下周汇报"},
                meta_json={"action_group_id": "group-1", "revision": 1},
            )
        )
        self.db.commit()

        delete_conversation(self.db, self.user.id, thread.id)

        self.assertIsNone(self.db.get(type(thread), thread.id))
        self.assertIsNone(
            self.db.scalar(
                select(ConversationPendingState).where(
                    ConversationPendingState.conversation_id == thread.id
                )
            )
        )
        self.assertIsNone(self.db.get(ApprovalRequest, approval.id))

    def test_delete_conversation_removes_agent_runs_and_tool_audits(self) -> None:
        from app.models import AgentRun, AgentToolCallAudit

        thread = create_conversation(self.db, self.user.id)
        run = AgentRun(
            user_id=self.user.id,
            workflow="general_chat",
            status="completed",
            conversation_id=thread.id,
            stream_status="completed",
            input_json={},
            output_json={},
        )
        self.db.add(run)
        self.db.flush()
        self.db.add(
            AgentToolCallAudit(
                agent_run_id=run.id,
                tool_name="dispatch_notification",
                request_json={},
                response_json={},
                status="ok",
            )
        )
        self.db.commit()

        delete_conversation(self.db, self.user.id, thread.id)

        self.assertIsNone(self.db.get(type(thread), thread.id))
        self.assertIsNone(self.db.get(type(run), run.id))
        audits = self.db.scalars(select(AgentToolCallAudit)).all()
        self.assertEqual(audits, [])

    def test_rewind_last_turn_restores_user_payload(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        attachment = Attachment(
            user_id=self.user.id,
            file_name="agenda.pdf",
            content_type="application/pdf",
            source_type="attachment",
            object_key="attachments/agenda-rewind.pdf",
            storage_bucket="synora",
            size_bytes=2048,
            status="uploaded",
        )
        self.db.add(attachment)
        self.db.commit()
        self.db.refresh(attachment)

        _, user_message, assistant_message, _ = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(
                text_content="帮我处理这个附件",
                attachment_ids=[attachment.id],
                selected_tool="schedule",
            ),
        )
        restored_thread, restored_message = rewind_last_turn(self.db, self.user.id, thread.id)
        self.assertEqual(restored_thread.id, thread.id)
        self.assertEqual(restored_message.text_content, user_message.text_content)
        self.assertEqual((restored_message.structured_payload_json or {}).get("selected_tool"), "schedule")
        refs = list((restored_message.structured_payload_json or {}).get("attachment_refs") or [])
        self.assertEqual(len(refs), 1)
        self.assertIsNone(self.db.get(ConversationMessage, user_message.id))
        self.assertIsNone(self.db.get(ConversationMessage, assistant_message.id))

    def test_rewind_last_turn_rejects_latest_user_message_with_card_below(self) -> None:
        thread = create_conversation(self.db, self.user.id)
        user_message = ConversationMessage(
            conversation_id=thread.id,
            role="user",
            message_type="text",
            status="completed",
            text_content="帮我创建明天下午的理发日程",
            structured_payload_json={},
            revision=1,
        )
        card_message = ConversationMessage(
            conversation_id=thread.id,
            role="assistant",
            message_type="schedule_draft_card",
            status="completed",
            structured_payload_json={"lifecycle_status": "approval_pending"},
            revision=1,
        )
        self.db.add(user_message)
        self.db.add(card_message)
        self.db.commit()

        with self.assertRaisesRegex(ValueError, "当前消息下方已有卡片，不能编辑重发。"):
            rewind_last_turn(self.db, self.user.id, thread.id)

        self.assertIsNotNone(self.db.get(ConversationMessage, user_message.id))
        self.assertIsNotNone(self.db.get(ConversationMessage, card_message.id))

    def test_delete_quick_note_removes_note(self) -> None:
        note = QuickNote(
            user_id=self.user.id,
            content="测试速记",
            tags_csv="科研",
            source_text="测试速记",
            source_type="text",
            source_attachment_ids=[],
            topic_tags_json=["科研"],
        )
        self.db.add(note)
        self.db.commit()
        self.db.refresh(note)

        delete_note(self.db, self.user.id, note.id)

        self.assertIsNone(self.db.get(QuickNote, note.id))

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="蓝桥杯安排")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_schedule_regeneration_keeps_user_history_in_source_text(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        original = self._draft().model_copy(
            update={
                "title": "蓝桥杯国赛",
                "details": "用户下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛，比赛时间是 9:00-13:00。",
                "source_text": "我下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛了，比赛时间是9:00-13:00",
            }
        )
        revised = original.model_copy(
            update={
                "details": "用户下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛，比赛时间是 9:00-13:00。",
                "source_text": "我下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛了，比赛时间是9:00-13:00\n\n不对，是下周",
            }
        )
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft-1"),
                {
                    "status": "ok",
                    "draft": original.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-1",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["比赛时间是 9:00-13:00"],
                    "parse_confidence": 0.91,
                },
            ),
            (
                SimpleNamespace(content="conflicts-1"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token-1",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash-1",
                    },
                },
            ),
            (
                SimpleNamespace(content="draft-2"),
                {
                    "status": "ok",
                    "draft": revised.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-2",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["最新更正为下周"],
                    "parse_confidence": 0.96,
                },
            ),
            (
                SimpleNamespace(content="conflicts-2"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token-2",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash-2",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, _, first_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(
                text_content="我下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛了，比赛时间是9:00-13:00",
                selected_tool="schedule",
            ),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, first_run.stream_token)]

        _, _, _, second_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="不对，是下周"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, second_run.stream_token)]

        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        latest_draft = ScheduleEventDraft.model_validate(pending.payload_json)
        self.assertEqual(
            latest_draft.source_text,
            "我下下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛了，比赛时间是9:00-13:00\n\n不对，是下周",
        )
        self.assertEqual(
            latest_draft.details,
            "用户下周周六要去沈阳东北大学浑南校区比赛蓝桥杯国赛，比赛时间是 9:00-13:00。",
        )

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="教学例会")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_pending_schedule_regenerates_new_revision_with_cards(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        original = self._draft()
        revised = original.model_copy(
            update={
                "source_text": "改成下周二下午三点在学院会议室开教学例会",
                "start": EventDateTimeValue(
                    dateTime=datetime.fromisoformat("2026-05-26T15:00:00+08:00"),
                    timeZone="Asia/Shanghai",
                ),
                "end": EventDateTimeValue(
                    dateTime=datetime.fromisoformat("2026-05-26T16:00:00+08:00"),
                    timeZone="Asia/Shanghai",
                ),
            }
        )
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": original.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-1",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["明天下午三点"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts-1"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token-1",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash-1",
                    },
                },
            ),
            (
                SimpleNamespace(content="draft-2"),
                {
                    "status": "ok",
                    "draft": revised.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-2",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["下周二下午三点"],
                    "parse_confidence": 0.96,
                },
            ),
            (
                SimpleNamespace(content="conflicts-2"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token-2",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash-2",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, _, first_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午三点在学院会议室开教学例会", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, first_run.stream_token)]

        _, _, _, second_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="改成下周二下午三点"),
        )
        second_events = [item async for item in consume_stream(self.db, self.user.id, thread.id, second_run.stream_token)]

        self.assertFalse(any("当前还有一项待确认内容" in str(item) for item in second_events))
        cards = [item["data"]["message"] for item in second_events if item["event"] == "card_snapshot"]
        self.assertEqual([item["revision"] for item in cards], [2, 2, 1])
        history = self.db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == thread.id)
            .order_by(ConversationMessage.id.asc())
        ).all()
        old_cards = [item for item in history if item.action_group_id == cards[0]["action_group_id"] and item.revision == 1]
        self.assertTrue(old_cards)
        self.assertTrue(all((item.structured_payload_json or {}).get("lifecycle_status") == "superseded" for item in old_cards))
        self.assertTrue(all((item.structured_payload_json or {}).get("is_actionable") is False for item in old_cards))
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertEqual(int(pending.meta_json.get("revision") or 0), 2)

    def test_superseded_approval_token_is_rejected(self) -> None:
        from app.domains.approval.service import create_approval_request, consume_approval_request

        approval1, token1 = create_approval_request(
            self.db,
            user_id=self.user.id,
            action="update_schedule",
            payload={"schedule_id": 1},
            draft_hash="draft-1",
            normalized_payload={"title": "旧预检"},
            evidence_digest=[],
            approval_scope="schedule:update:1",
        )
        approval2, token2 = create_approval_request(
            self.db,
            user_id=self.user.id,
            action="update_schedule",
            payload={"schedule_id": 1},
            draft_hash="draft-2",
            normalized_payload={"title": "新预检"},
            evidence_digest=[],
            approval_scope="schedule:update:1",
        )
        self.assertEqual(approval2.status, "pending")
        refreshed = self.db.get(type(approval1), approval1.id)
        self.assertEqual(refreshed.status, "superseded")
        with self.assertRaises(ValueError):
            consume_approval_request(
                self.db,
                user_id=self.user.id,
                action="update_schedule",
                approval_token=token1,
                draft_hash="draft-1",
            )
        consume_approval_request(
            self.db,
            user_id=self.user.id,
            action="update_schedule",
            approval_token=token2,
            draft_hash="draft-2",
        )

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="创建提醒")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_conversation_without_selected_tool_auto_routes_to_schedule_intake(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        draft = self._draft()
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": draft.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["明天下午"],
                    "parse_confidence": 0.92,
                },
            ),
            (
                SimpleNamespace(content="conflicts"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="明天下午我去剪头发"),
        )

        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        self.assertEqual(events[-1]["event"], "run_completed")
        self.assertTrue(any(item["event"] == "approval_required" for item in events))
        self.assertNotIn("请先选择", self.db.get(type(assistant_message), assistant_message.id).text_content or "")
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNotNone(pending)
        self.assertEqual(pending.stage, "approval_pending")

    async def test_aroute_conversation_intent_injects_recent_history(self) -> None:
        with patch(
            "app.agent.llm.ainvoke_structured",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(workflow="general_chat"),
        ) as structured_mock:
            result = await llm.aroute_conversation_intent(
                get_settings(),
                {
                    "text_content": "这周六下午左右吧",
                    "attachment_ids": [],
                    "selected_tool": None,
                    "context": {
                        "conversation_history_lines": [
                            "用户：我过几天想去银行办一张万事达卡",
                            "助手：我已经为你生成日程草稿…",
                        ]
                    },
                },
            )
        self.assertEqual(result, "general_chat")
        call_kwargs = structured_mock.await_args.kwargs
        payload = json.loads(call_kwargs["user_text"])
        self.assertEqual(
            payload["recent_history"],
            ["用户：我过几天想去银行办一张万事达卡", "助手：我已经为你生成日程草稿…"],
        )
        self.assertIn("补充、修正或继续", call_kwargs["system_prompt"])
        self.assertIn("schedule_intake", call_kwargs["system_prompt"])

    async def test_aroute_conversation_intent_has_tightened_rules(self) -> None:
        """R3：收紧后的路由规则必须在 system_prompt 中——泛咨询（如"帮我规划旅行"）
        无明确时间锚点/未要求创建日程时仍归 general_chat。"""
        with patch(
            "app.agent.llm.ainvoke_structured",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(workflow="general_chat"),
        ) as structured_mock:
            result = await llm.aroute_conversation_intent(
                get_settings(),
                {
                    "text_content": "帮我规划旅行",
                    "attachment_ids": [],
                    "selected_tool": None,
                    "context": {},
                },
            )
        self.assertEqual(result, "general_chat")
        system_prompt = structured_mock.await_args.kwargs["system_prompt"]
        self.assertIn("明确要求创建可提醒的日程", system_prompt)
        self.assertIn("仅泛泛表达", system_prompt)
        self.assertIn("未要求创建日程", system_prompt)

    async def test_abort_emits_run_cancelled_and_marks_cancelled(self) -> None:
        """R6：发送中点击停止 → abort_stream 命中检查点 → run_cancelled 收口，DB 落 cancelled。"""
        started = asyncio.Event()

        async def fake_astream(initial, config, stream_mode=None):
            stream_id = initial.get("stream_id")
            yield "updates", {}
            started.set()
            # 模拟跨 worker 的 LLM 长时间生成：运行 worker 只检查持久化状态。
            while True:
                try:
                    raise_if_stream_cancelled(
                        self.db,
                        stream_id,
                        force_database_check=True,
                    )
                except AgentRunCancelled:
                    raise
                await asyncio.sleep(0)

        with patch(
            "app.agent.graph.build_graph",
            return_value=SimpleNamespace(astream=fake_astream),
        ), patch(
            "app.agent.checkpointer.setup_checkpointer",
            new_callable=AsyncMock,
        ):
            thread = create_conversation(self.db, self.user.id)
            _, _, _, agent_run = queue_message(
                self.db,
                self.user.id,
                thread.id,
                ConversationSendMessageRequest(text_content="你好"),
            )
            events_future = asyncio.ensure_future(
                _collect_stream(
                    consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)
                )
            )
            await asyncio.wait_for(started.wait(), timeout=5)
            self.assertTrue(
                abort_stream(
                    self.db,
                    user_id=self.user.id,
                    conversation_id=thread.id,
                    stream_id=agent_run.stream_token,
                )
            )
            events = await asyncio.wait_for(events_future, timeout=5)

        self.assertEqual(events[-1]["event"], "run_cancelled")
        self.assertEqual(events[-1]["data"]["stream_status"], "cancelled")
        agent_run = self.db.get(AgentRun, agent_run.id)
        self.assertEqual(agent_run.status, "cancelled")
        self.assertEqual(agent_run.stream_status, "cancelled")
        self.assertEqual(agent_run.output_json.get("completion_status"), "cancelled")
        # abort 后取消标记被清理，防内存泄漏
        self.assertFalse(is_stream_cancelled(agent_run.stream_token))

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.intake_service.invoke_synora_tool", new_callable=AsyncMock)
    @patch("app.agent.llm.generate_conversation_title", return_value="创建提醒")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="schedule_intake")
    async def test_followup_message_resumes_last_draft_card_without_pending(
        self,
        _intent_mock,
        _title_mock,
        invoke_tool_mock,
        _write_memory_mock,
    ) -> None:
        original = self._draft()
        revised = original.model_copy(
            update={
                "source_text": "这周六下午去银行办万事达卡",
                "start": EventDateTimeValue(
                    dateTime=datetime.fromisoformat("2026-08-15T14:00:00+08:00"),
                    timeZone="Asia/Shanghai",
                ),
                "end": EventDateTimeValue(
                    dateTime=datetime.fromisoformat("2026-08-15T15:00:00+08:00"),
                    timeZone="Asia/Shanghai",
                ),
            }
        )
        invoke_tool_mock.side_effect = [
            (
                SimpleNamespace(content="draft"),
                {
                    "status": "ok",
                    "draft": original.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-1",
                    "missing_fields": ["start_at"],
                    "ambiguity_flags": ["time_ambiguous"],
                    "evidence_digest": ["过几天去银行办卡"],
                    "parse_confidence": 0.7,
                },
            ),
            (
                SimpleNamespace(content="draft-2"),
                {
                    "status": "ok",
                    "draft": revised.model_dump(mode="json", by_alias=True),
                    "draft_hash": "draft-hash-2",
                    "missing_fields": [],
                    "ambiguity_flags": [],
                    "evidence_digest": ["周六下午"],
                    "parse_confidence": 0.9,
                },
            ),
            (
                SimpleNamespace(content="conflicts-2"),
                {
                    "status": "ok",
                    "conflict_items": [],
                    "suggestions": [],
                    "risk_level": "low",
                    "approval": {
                        "approval_token": "approval-token-2",
                        "action": "create_schedule",
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "draft_hash": "draft-hash-2",
                    },
                },
            ),
        ]
        thread = create_conversation(self.db, self.user.id)
        _, _, _, first_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="我过几天想去银行办一张万事达卡", selected_tool="schedule"),
        )
        _ = [item async for item in consume_stream(self.db, self.user.id, thread.id, first_run.stream_token)]

        # 模拟 pending 被清理（dismiss/超时），仅剩上一条未决草稿卡
        pending = self.db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == thread.id))
        self.assertIsNotNone(pending)
        self.db.delete(pending)
        self.db.commit()

        _, _, _, second_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="这周六下午左右吧"),
        )
        second_events = [item async for item in consume_stream(self.db, self.user.id, thread.id, second_run.stream_token)]

        self.assertTrue(any(item["event"] == "approval_required" for item in second_events))
        self.assertFalse(any("请先选择" in str(item) for item in second_events))
        # Fix 4 兜底：从草稿卡重建 context，previous_draft_summary 进入 parse_schedule_draft
        second_tool_payload = invoke_tool_mock.await_args_list[1].args[1]
        self.assertEqual(second_tool_payload["context"]["pending_regeneration"], "schedule")
        self.assertEqual(second_tool_payload["context"]["supersede_action_group_id"], second_tool_payload["context"]["pending_action_group_id"])
        self.assertIn("教学例会", second_tool_payload["context"]["previous_draft_summary"])

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.agent.llm.create_chat_model", return_value=_FakeStreamingChatModel(text="好的"))
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=_fake_ainvoke_structured)
    @patch("app.agent.llm.generate_conversation_title", return_value="测试聊天")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    async def test_general_chat_never_binds_intake_tools(
        self,
        _extract_mock,
        memory_mock,
        _intent_mock,
        _title_mock,
        _ainvoke_mock,
        _chat_model_mock,
        _write_memory_mock,
    ) -> None:
        memory_mock.return_value = SimpleNamespace(summary="", items=[])
        thread = create_conversation(self.db, self.user.id)
        _, _, _, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="你好"),
        )
        with patch(
            "app.domains.conversation.agent_service.build_agent_tools",
            new_callable=AsyncMock,
            return_value=[],
        ) as tools_mock:
            events = [
                item
                async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)
            ]

        # general_chat 只绑定只读原生工具白名单（get_current_time / web_search）：
        # intake 写工具依旧不注入，observe 阶段同样白名单匹配，杜绝"未知工具"空转。
        self.assertEqual(tools_mock.await_count, 1)
        self.assertEqual(tools_mock.await_args.kwargs["include_names"], {"get_current_time", "web_search"})
        self.assertTrue(any(item["event"] == "run_completed" for item in events))

    @patch("app.domains.conversation.stream_runtime.write_user_memory.delay")
    @patch("app.domains.conversation.service.MemoryService.extract_memory_facts", return_value=[])
    @patch("app.domains.conversation.service.MemoryService.retrieve_context")
    @patch("app.agent.llm.aroute_conversation_intent", new_callable=AsyncMock, return_value="general_chat")
    @patch("app.agent.llm.generate_conversation_title", return_value="时间问答")
    @patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock)
    @patch(
        "app.agent.llm.create_chat_model",
        return_value=_ToolThenTextChatModel(
            tool_call={"name": "get_current_time", "args": {}, "id": "call-1", "type": "tool_call"},
            final_text="现在是周四 15:30。",
        ),
    )
    @patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock)
    async def test_general_chat_time_tool_call_executes(
        self,
        tools_mock,
        _chat_model_mock,
        _ainvoke_mock,
        _title_mock,
        _intent_mock,
        memory_mock,
        _extract_mock,
        _write_memory_mock,
    ) -> None:
        tools_mock.return_value = [_FakeTimeTool()]
        memory_mock.return_value = SimpleNamespace(summary="", items=[])

        reflect_calls = {"n": 0}

        async def _reflect_side_effect(settings, **kwargs):
            operation = kwargs.get("operation")
            if operation == "agent_plan":
                return llm.PlanResult(plan="回答用户")
            if operation == "agent_reflect":
                reflect_calls["n"] += 1
                if reflect_calls["n"] == 1:
                    return llm.ReflectDecision(
                        is_complete=False,
                        rationale="需要时间信息",
                        follow_up_prompt="结合工具返回的时间给出回答",
                    )
                return llm.ReflectDecision(is_complete=True, rationale="信息已充分")
            raise AssertionError(f"unexpected ainvoke_structured operation: {operation}")

        _ainvoke_mock.side_effect = _reflect_side_effect

        thread = create_conversation(self.db, self.user.id)
        _, _, assistant_message, agent_run = queue_message(
            self.db,
            self.user.id,
            thread.id,
            ConversationSendMessageRequest(text_content="现在几点了"),
        )
        events = [item async for item in consume_stream(self.db, self.user.id, thread.id, agent_run.stream_token)]

        # act 只绑定 get_current_time，observe 白名单匹配并执行该工具。
        bound_names = [t.name for t in _chat_model_mock.return_value.bound_tools]
        self.assertEqual(bound_names, ["get_current_time"])
        # 每轮 act 绑定一次 + 有 tool_calls 的 observe 执行一次（首轮 act/observe + 第二轮 act）。
        self.assertGreaterEqual(tools_mock.await_count, 2)
        for call in tools_mock.await_args_list:
            self.assertEqual(call.kwargs["include_names"], {"get_current_time", "web_search"})
        self.assertTrue(
            any(
                item["event"] == "tool_call_started" and item["data"]["tool_name"] == "get_current_time"
                for item in events
            )
        )
        self.assertTrue(
            any(
                item["event"] == "tool_call_completed" and item["data"]["tool_name"] == "get_current_time"
                for item in events
            )
        )
        # 第二轮 act 结合工具结果输出最终文本，run_completed 收口。
        self.assertEqual(self.db.get(type(assistant_message), assistant_message.id).text_content, "现在是周四 15:30。")
        self.assertEqual(events[-1]["event"], "run_completed")


if __name__ == "__main__":
    unittest.main()
