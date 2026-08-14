"""Agent 执行服务（plan/reflect 降级与确定性计划）测试。"""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agent import llm
from app.config import get_settings
from app.db import Base
from app.domains.conversation.agent_service import (
    _merge_streamed_tool_calls,
    act_step,
    observe_step,
    plan_step,
    reflect_step,
    serialize_tool_calls,
)
from app.models import ConversationMessage, ConversationThread, User


class AgentServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
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
        user = User(
            email="agent@example.com",
            display_name="Agent 测试",
            password_hash="hashed-password",
            is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        thread = ConversationThread(user_id=user.id, title="Agent 测试")
        self.db.add(thread)
        self.db.commit()
        self.thread = thread
        self.message = ConversationMessage(
            conversation_id=thread.id,
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
        self.engine.dispose()

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

    async def test_plan_step_uses_deterministic_plan_for_simple_question(self) -> None:
        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock) as llm_mock:
            result = await plan_step(self.db, self.thread, self.message, SimpleNamespace(), state=self._state(), emit=self.events.append)

        llm_mock.assert_not_awaited()
        self.assertEqual(result["plan"], "回答用户问题")
        step = result["steps"][0]
        self.assertEqual(step["plan_source"], "deterministic")
        self.assertFalse(step.get("degraded"))

    async def test_plan_step_calls_llm_for_non_trivial_request(self) -> None:
        with patch(
            "app.agent.llm.ainvoke_structured",
            new_callable=AsyncMock,
            return_value=llm.PlanResult(plan="查询日程并汇总"),
        ) as llm_mock:
            result = await plan_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(),
                state=self._state(user_message="帮我看看这周的工作安排，顺便整理一下下周的出差计划"),
                emit=self.events.append,
            )

        llm_mock.assert_awaited_once()
        self.assertEqual(llm_mock.await_args.kwargs["operation"], "agent_plan")
        self.assertEqual(result["plan"], "查询日程并汇总")
        self.assertEqual(result["steps"][0]["plan_source"], "llm")

    async def test_plan_step_logs_structured_warning_on_llm_failure(self) -> None:
        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock, side_effect=RuntimeError("llm down")):
            with self.assertLogs("app.domains.conversation.agent_service", level="WARNING") as captured:
                result = await plan_step(
                    self.db,
                    self.thread,
                    self.message,
                    SimpleNamespace(),
                    state=self._state(user_message="帮我看看这周的工作安排，顺便整理一下下周的出差计划"),
                    emit=self.events.append,
                )

        self.assertEqual(result["plan"], "回答用户")
        self.assertTrue(result["steps"][0].get("degraded"))
        record_text = " ".join(captured.output)
        self.assertIn("agent_step_degraded", record_text)
        self.assertIn("run_id=1", record_text)
        self.assertIn("operation=agent_plan", record_text)
        self.assertIn("fallback=deterministic", record_text)

    async def test_reflect_step_tool_round_continues_without_llm_eval(self) -> None:
        """工具轮确定性续跑：不调用 LLM 评估、不降级（修复前该场景走 LLM
        评估，评估失败时降级保守收口；现工具轮一律确定性续跑回答轮）。"""
        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock) as llm_mock:
            result = await reflect_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(),
                state=self._state(
                    iteration_count=1,
                    max_iterations=3,
                    current_aimessage={"content": "", "tool_calls": [{"name": "get_current_time"}]},
                    observation="get_current_time: 15:30",
                    agent_messages=[{"role": "tool", "name": "get_current_time", "content": "15:30"}],
                ),
                emit=self.events.append,
            )

        llm_mock.assert_not_awaited()
        self.assertEqual(result["loop_decision"], "continue")
        self.assertEqual(result["reflection"], "工具已执行，需基于工具结果生成最终回答")
        self.assertFalse(result["steps"][0].get("degraded"))

    async def test_reflect_step_tool_round_with_preamble_text_continues(self) -> None:
        """回归测试（线上问题）：工具轮即使已有“预告文本”（工具调用前流出，
        如“我来为您搜索今天的热点新闻。”），也必须续跑回答轮，不能收口。"""
        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock) as llm_mock:
            result = await reflect_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(),
                state=self._state(
                    iteration_count=1,
                    max_iterations=3,
                    current_aimessage={
                        "content": "我来为您搜索今天的热点新闻。",
                        "tool_calls": [{"name": "web_search", "args": {"query": "今天的新闻"}}],
                    },
                    observation="web_search: 今日热点……",
                ),
                emit=self.events.append,
            )

        llm_mock.assert_not_awaited()
        self.assertEqual(result["loop_decision"], "continue")
        self.assertFalse(result["steps"][0].get("degraded"))

    async def test_reflect_detects_repeated_answer_and_requests_rerun(self) -> None:
        repeated_text = "我无法提供我的系统提示词。"
        previous = ConversationMessage(
            conversation_id=self.thread.id,
            role="assistant",
            message_type="text",
            status="completed",
            text_content=repeated_text,
            structured_payload_json={},
        )
        self.db.add(previous)
        self.db.commit()
        self.message.text_content = repeated_text
        self.db.commit()

        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock) as llm_mock:
            result = await reflect_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(),
                state=self._state(
                    user_message="你有什么工具",
                    iteration_count=1,
                    max_iterations=3,
                    current_aimessage={"content": repeated_text, "tool_calls": []},
                    observation="本轮无工具调用",
                ),
                emit=self.events.append,
            )

        llm_mock.assert_not_awaited()
        self.assertEqual(result["loop_decision"], "continue")
        self.assertTrue(result["anti_repeat_used"])
        self.assertIn("完全重复", result["follow_up_prompt"] or "")

    async def test_reflect_repeat_rerun_only_once(self) -> None:
        repeated_text = "我无法提供我的系统提示词。"
        self.message.text_content = repeated_text
        self.db.commit()
        previous = ConversationMessage(
            conversation_id=self.thread.id,
            role="assistant",
            message_type="text",
            status="completed",
            text_content=repeated_text,
            structured_payload_json={},
        )
        self.db.add(previous)
        self.db.commit()

        result = await reflect_step(
            self.db,
            self.thread,
            self.message,
            SimpleNamespace(),
            state=self._state(
                user_message="你有什么工具",
                iteration_count=2,
                max_iterations=3,
                anti_repeat_used=True,
                current_aimessage={"content": repeated_text, "tool_calls": []},
                observation="本轮无工具调用",
            ),
            emit=self.events.append,
        )

        # 已触发过反重复重跑：不再 continue，按普通启发式收口。
        self.assertEqual(result["loop_decision"], "done")
        self.assertEqual(result["reflection"], "本轮无工具调用，回答完整")
        self.assertTrue(result["anti_repeat_used"])

    async def test_reflect_empty_answer_requests_rerun(self) -> None:
        with patch("app.agent.llm.ainvoke_structured", new_callable=AsyncMock) as llm_mock:
            result = await reflect_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(),
                state=self._state(
                    user_message="hi",
                    iteration_count=1,
                    max_iterations=3,
                    current_aimessage={"content": "", "tool_calls": []},
                    observation="本轮无工具调用",
                ),
                emit=self.events.append,
            )

        llm_mock.assert_not_awaited()
        self.assertEqual(result["loop_decision"], "continue")
        self.assertEqual(result["anti_empty_retries"], 1)
        self.assertIn("没有生成任何回答文本", result["follow_up_prompt"] or "")

    async def test_reflect_empty_answer_retries_bounded_then_fallback_close(self) -> None:
        # 第一次重试（retries=1）：继续重跑
        result = await reflect_step(
            self.db,
            self.thread,
            self.message,
            SimpleNamespace(),
            state=self._state(
                user_message="hi",
                iteration_count=2,
                max_iterations=3,
                anti_empty_retries=1,
                current_aimessage={"content": "", "tool_calls": []},
                observation="本轮无工具调用",
            ),
            emit=self.events.append,
        )
        self.assertEqual(result["loop_decision"], "continue")
        self.assertEqual(result["anti_empty_retries"], 2)

        # 重试用尽（retries=2）：兜底收口并标记降级
        result = await reflect_step(
            self.db,
            self.thread,
            self.message,
            SimpleNamespace(),
            state=self._state(
                user_message="hi",
                iteration_count=3,
                max_iterations=3,
                anti_empty_retries=2,
                current_aimessage={"content": "", "tool_calls": []},
                observation="本轮无工具调用",
            ),
            emit=self.events.append,
        )
        self.assertEqual(result["loop_decision"], "done")
        self.assertEqual(result["anti_empty_retries"], 2)
        self.assertIn("兜底文案收口", result["reflection"])
        self.assertTrue(result["steps"][0].get("degraded"))

    def test_general_chat_system_prompt_forbids_repetition(self) -> None:
        with (
            patch("app.agent.llm.create_chat_model") as model_mock,
            patch("app.agent.llm.create_agent", return_value=SimpleNamespace()) as agent_mock,
        ):
            llm.build_general_chat_agent(get_settings(), [])

        self.assertIsNotNone(model_mock.call_args)
        prompt = agent_mock.call_args.kwargs["system_prompt"]
        self.assertIn("只回答「当前输入」", prompt)
        self.assertIn("禁止重复、复述或延续上一轮", prompt)
        self.assertIn("web_search", prompt)

    async def test_act_step_resets_message_on_anti_repeat_rerun(self) -> None:
        self.message.text_content = "重复的旧回答"
        self.db.commit()
        events: list[dict] = []

        class _StreamModel:
            def bind_tools(self, tools):
                return self

            async def astream(self, messages):
                from langchain_core.messages import AIMessageChunk

                yield AIMessageChunk(content="这是针对当前输入的新回答")

        with (
            patch("app.domains.conversation.agent_service.MemoryService.retrieve_context", return_value=SimpleNamespace(summary="", items=[])),
            patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[]),
            patch("app.agent.llm.create_chat_model", return_value=_StreamModel()),
        ):
            result = await act_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(),
                state=self._state(user_message="再问一次", anti_repeat_used=True),
                emit=events.append,
            )

        # 首条为 running 的 act 推理步骤，随后是 message_reset，再是新回答 delta。
        self.assertEqual(events[1]["event"], "message_reset")
        self.assertIn("message_reset", [item["event"] for item in events])
        self.assertEqual(self.message.text_content, "这是针对当前输入的新回答")
        self.assertIn("这是针对当前输入的新回答", result["aimessage"]["content"])

    def test_merge_streamed_tool_calls_accumulates_partial_deltas(self) -> None:
        # 完整列表直接合并
        full_chunk = SimpleNamespace(
            tool_calls=[{"name": "get_current_time", "args": {}, "id": "c1"}],
            tool_call_chunks=[],
        )
        accumulated = _merge_streamed_tool_calls(full_chunk, [])
        self.assertEqual(accumulated[0]["name"], "get_current_time")

        # dashscope 兼容模式：真实调用之后追加空名占位条目，不得覆盖已捕获的调用。
        placeholder_chunk = SimpleNamespace(
            tool_calls=[
                {"name": "", "args": {}, "id": "", "type": "tool_call"},
                {"name": "", "args": {}, "id": "", "type": "tool_call"},
            ],
            tool_call_chunks=[],
        )
        accumulated = _merge_streamed_tool_calls(placeholder_chunk, accumulated)
        self.assertEqual(accumulated[0]["name"], "get_current_time")
        self.assertEqual(accumulated[0]["id"], "c1")
        serialized = serialize_tool_calls(accumulated)
        self.assertEqual(serialized[0]["args"], {})
        self.assertEqual(serialized[0]["id"], "c1")

        # 部分增量按 index 累积：name 一次性到齐，args 分片累积
        partial_chunk = SimpleNamespace(
            tool_calls=None,
            tool_call_chunks=[
                SimpleNamespace(index=0, name="web_search", args='{"query": "天气', id=""),
                SimpleNamespace(index=0, name="", args='"}', id="c2"),
            ],
        )
        merged = _merge_streamed_tool_calls(partial_chunk, [])
        self.assertEqual(merged[0]["name"], "web_search")
        self.assertEqual(merged[0]["id"], "c2")
        self.assertEqual(merged[0]["args"], '{"query": "天气"}')

        # 序列化时还原 JSON 参数，并丢弃名称为空的残缺调用
        serialized = serialize_tool_calls(merged)
        self.assertEqual(serialized[0]["args"], {"query": "天气"})
        self.assertEqual(serialize_tool_calls([{"name": "", "args": {}, "id": "x"}]), [])

    def test_merge_streamed_tool_calls_interleaved_name_and_arg_fragments(self) -> None:
        """复刻 dashscope 真实流：首 chunk 带完整 name（args 空占位），
        随后 args 分片（dict 形态 tool_call_chunks）穿插空名占位条目，
        最终应拼出完整 JSON 参数。"""
        accumulated: list[dict] = []
        accumulated = _merge_streamed_tool_calls(
            SimpleNamespace(
                tool_calls=[{"name": "web_search", "args": {}, "id": "call-1", "type": "tool_call"}],
                tool_call_chunks=[{"name": "web_search", "args": "", "index": 0, "id": "call-1"}],
            ),
            accumulated,
        )
        accumulated = _merge_streamed_tool_calls(
            SimpleNamespace(
                tool_calls=[{"name": "", "args": {}, "id": "", "type": "tool_call"}],
                tool_call_chunks=[{"name": None, "args": '{"query": ', "index": 0, "id": ""}],
            ),
            accumulated,
        )
        accumulated = _merge_streamed_tool_calls(
            SimpleNamespace(tool_calls=[], tool_call_chunks=[{"name": None, "args": '"DeepSeek API', "index": 0, "id": ""}]),
            accumulated,
        )
        accumulated = _merge_streamed_tool_calls(
            SimpleNamespace(tool_calls=[], tool_call_chunks=[{"name": None, "args": ' 价格 2026', "index": 0, "id": ""}]),
            accumulated,
        )
        accumulated = _merge_streamed_tool_calls(
            SimpleNamespace(tool_calls=[], tool_call_chunks=[{"name": None, "args": '"}', "index": 0, "id": ""}]),
            accumulated,
        )
        accumulated = _merge_streamed_tool_calls(
            SimpleNamespace(
                tool_calls=[{"name": "", "args": {}, "id": "", "type": "tool_call"}],
                tool_call_chunks=[{"name": None, "args": "", "index": 0, "id": ""}],
            ),
            accumulated,
        )

        serialized = serialize_tool_calls(accumulated)
        self.assertEqual(serialized[0]["name"], "web_search")
        self.assertEqual(serialized[0]["args"], {"query": "DeepSeek API 价格 2026"})
        self.assertEqual(serialized[0]["id"], "call-1")

    async def test_reflect_llm_complete_without_text_forces_answer_round(self) -> None:
        """LLM 判定信息已充分但没有面向用户的回答文本时，必须强制再跑一轮生成回答。"""
        with patch(
            "app.agent.llm.ainvoke_structured",
            new_callable=AsyncMock,
            return_value=llm.ReflectDecision(is_complete=True, rationale="信息已充分"),
        ) as llm_mock:
            result = await reflect_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(),
                state=self._state(
                    user_message="帮我搜一下 deepseek 价格",
                    iteration_count=1,
                    max_iterations=3,
                    current_aimessage={"content": "", "tool_calls": [{"name": "web_search"}]},
                    observation="web_search: 搜索结果已返回",
                    agent_messages=[{"role": "tool", "name": "web_search", "content": "搜索结果内容"}],
                ),
                emit=self.events.append,
            )

        self.assertEqual(result["loop_decision"], "continue")
        self.assertIn("最终回答文本", result["follow_up_prompt"] or "")

    async def test_reflect_promise_only_answer_forces_tool_round(self) -> None:
        """搜索类请求只得到承诺话术而未调用工具时，强制再跑一轮并指向 web_search。"""
        result = await reflect_step(
            self.db,
            self.thread,
            self.message,
            SimpleNamespace(),
            state=self._state(
                user_message="帮我搜一下 deepseek 现在的 api 价格",
                iteration_count=1,
                max_iterations=4,
                current_aimessage={"content": "我来帮你搜索 DeepSeek API 的最新价格信息。", "tool_calls": []},
                observation="本轮无工具调用",
            ),
            emit=self.events.append,
        )

        self.assertEqual(result["loop_decision"], "continue")
        self.assertTrue(result["anti_commitment_used"])
        self.assertIn("web_search", result["follow_up_prompt"] or "")

    async def test_reflect_promise_guard_triggered_once(self) -> None:
        result = await reflect_step(
            self.db,
            self.thread,
            self.message,
            SimpleNamespace(),
            state=self._state(
                user_message="帮我搜一下 deepseek 现在的 api 价格",
                iteration_count=2,
                max_iterations=4,
                anti_commitment_used=True,
                current_aimessage={"content": "好的，我马上帮你搜索。", "tool_calls": []},
                observation="本轮无工具调用",
            ),
            emit=self.events.append,
        )

        self.assertEqual(result["loop_decision"], "done")
        self.assertEqual(result["reflection"], "本轮无工具调用，回答完整")

    async def test_reflect_normal_answer_not_marked_as_promise(self) -> None:
        result = await reflect_step(
            self.db,
            self.thread,
            self.message,
            SimpleNamespace(),
            state=self._state(
                user_message="什么是大语言模型",
                iteration_count=1,
                max_iterations=4,
                current_aimessage={
                    "content": "大语言模型（LLM）是基于海量文本训练、能理解和生成自然语言的深度学习模型。",
                    "tool_calls": [],
                },
                observation="本轮无工具调用",
            ),
            emit=self.events.append,
        )

        self.assertEqual(result["loop_decision"], "done")
        self.assertEqual(result["reflection"], "本轮无工具调用，回答完整")
        self.assertFalse(result.get("anti_commitment_used"))

    async def test_observe_web_search_falls_back_to_user_message_query(self) -> None:
        captured: dict = {}

        class _FakeWebSearchTool:
            name = "web_search"

            async def ainvoke(self, args):
                captured.update(args)
                return {"status": "ok", "content": "deepseek 价格 ...", "references": []}

        with patch(
            "app.domains.conversation.agent_service.build_agent_tools",
            new_callable=AsyncMock,
            return_value=[_FakeWebSearchTool()],
        ):
            result = await observe_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(id=1),
                state=self._state(
                    user_message="帮我搜一下 deepseek 现在的 api 价格",
                    pending_tool_calls=[{"name": "web_search", "args": {}, "id": "c1"}],
                ),
                emit=self.events.append,
            )

        self.assertFalse(result["tool_failed"])
        self.assertEqual(captured["query"], "帮我搜一下 deepseek 现在的 api 价格")
        self.assertIn("deepseek 价格", result["observation"])

    async def test_observe_empty_name_tool_call_fails_closed(self) -> None:
        with patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[]):
            result = await observe_step(
                self.db,
                self.thread,
                self.message,
                SimpleNamespace(id=1),
                state=self._state(pending_tool_calls=[{"name": "", "args": {}, "id": "c1"}]),
                emit=self.events.append,
            )

        self.assertTrue(result["tool_failed"])
        self.assertIn("未给出工具名", result["observation"])
        self.assertIn("未给出工具名", str(self.events))

    async def test_build_recent_history_lines_returns_recent_window(self) -> None:
        """intake 近期历史窗口：返回当前消息之前的最近文本消息（时间正序、排除自身、
        跳过空文本占位），修复“日程/速记卡片缺少前面对话上下文”问题。"""
        from app.domains.conversation.intake_service import build_recent_history_lines

        for role, text in (
            ("user", "我明天下午想开个会"),
            ("assistant", "好的，我帮你记一下，需要定会议室吗？"),
            ("user", "对，学院会议室"),
        ):
            self.db.add(
                ConversationMessage(
                    conversation_id=self.thread.id,
                    role=role,
                    message_type="text",
                    status="completed",
                    text_content=text,
                    structured_payload_json={},
                )
            )
        self.db.commit()
        current = ConversationMessage(
            conversation_id=self.thread.id,
            role="user",
            message_type="text",
            status="streaming",
            text_content="把刚才说的那个会议加上",
            structured_payload_json={},
        )
        self.db.add(current)
        self.db.commit()

        lines = build_recent_history_lines(self.db, self.thread, current.id, limit=8)

        joined = "\n".join(lines)
        self.assertIn("我明天下午想开个会", joined)
        self.assertIn("学院会议室", joined)
        self.assertIn("助手：", joined)
        self.assertNotIn("把刚才说的那个会议加上", joined)
        self.assertEqual(lines[0].startswith("用户"), True)


if __name__ == "__main__":
    unittest.main()
