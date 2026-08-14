"""流式文本批量落库（MessageTextBuffer / emit_text_stream）测试。

覆盖：按字符数批量刷新、按时间窗口刷新、完成强制刷新、取消强制刷新、
失败强制刷新，以及 act_step 在模型异常时仍把已推送文本落库。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.conversation.agent_service import act_step
from app.domains.conversation.stream_runtime import (
    AgentRunCancelled,
    MessageTextBuffer,
    emit_text_stream,
    raise_if_stream_cancelled,
)
from app.models import ConversationMessage, ConversationThread, User


class _FailingStreamModel:
    """先吐一段文本，随后抛异常，模拟 LLM 流中断。"""

    def __init__(self, chunk: str, error: Exception) -> None:
        self._chunk = chunk
        self._error = error

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        from langchain_core.messages import AIMessageChunk

        yield AIMessageChunk(content=self._chunk)
        raise self._error


class StreamRuntimeTests(unittest.IsolatedAsyncioTestCase):
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
            email="stream@example.com",
            display_name="流式测试",
            password_hash="hashed-password",
            is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        self.user_id = user.id
        thread = ConversationThread(user_id=user.id, title="流式测试")
        self.db.add(thread)
        self.db.commit()
        self.thread_id = thread.id
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

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _fresh_db(self) -> Session:
        return self.session_factory()

    def _persisted_text(self) -> str | None:
        return self._fresh_db().get(ConversationMessage, self.message.id).text_content

    def test_buffer_batches_writes_by_char_count(self) -> None:
        buffer = MessageTextBuffer(self.db, self.message, flush_chars=40, flush_interval_seconds=60)
        buffer.append("a" * 39)
        self.assertFalse(buffer.needs_flush())
        self.assertEqual(self._persisted_text(), "")
        buffer.append("b")
        self.assertTrue(buffer.needs_flush())
        buffer.flush()
        self.assertEqual(self._persisted_text(), "a" * 39 + "b")
        self.assertFalse(buffer.dirty)
        self.assertEqual(buffer.pending_chars, 0)

    def test_buffer_flushes_by_time_window(self) -> None:
        buffer = MessageTextBuffer(self.db, self.message, flush_chars=1000, flush_interval_seconds=0)
        buffer.append("一小段")
        self.assertTrue(buffer.needs_flush())
        buffer.flush()
        self.assertEqual(self._persisted_text(), "一小段")

    def test_buffer_set_text_forces_flush(self) -> None:
        buffer = MessageTextBuffer(self.db, self.message, flush_chars=1000, flush_interval_seconds=60)
        buffer.set_text("整体覆盖文本")
        self.assertTrue(buffer.needs_flush())
        buffer.flush()
        self.assertEqual(self._persisted_text(), "整体覆盖文本")

    async def test_emit_text_stream_completes_and_persists_all_text(self) -> None:
        events = []
        async for event in emit_text_stream(self.db, self.message, "这是一段用于验证完成强制刷新的文本内容", stream_id=None):
            events.append(event)

        self.assertEqual(len(events), len("这是一段用于验证完成强制刷新的文本内容") // 12 + 1)
        self.assertEqual(self._persisted_text(), "这是一段用于验证完成强制刷新的文本内容")
        refreshed = self._fresh_db().get(ConversationMessage, self.message.id)
        self.assertEqual(refreshed.status, "completed")

    async def test_emit_text_stream_flushes_on_cancel(self) -> None:
        calls = {"n": 0}

        def _raise_after_two(db, stream_id, **kwargs):
            calls["n"] += 1
            if calls["n"] > 2:
                raise AgentRunCancelled(stream_id or "test-stream")

        text = "这是一段足够长的取消验证文本，用来确认取消发生时缓冲会被强制刷新而不是丢失。"
        with patch("app.domains.conversation.stream_runtime.raise_if_stream_cancelled", side_effect=_raise_after_two):
            with self.assertRaises(AgentRunCancelled):
                async for _ in emit_text_stream(self.db, self.message, text, stream_id="test-stream"):
                    pass

        # 取消时已推送的 2 个 chunk 必须全部落库，客户端可见文本与 DB 一致。
        self.assertEqual(self._persisted_text(), text[:24])

    async def test_emit_text_stream_flushes_on_failure(self) -> None:
        text = "这是一段足够长的失败验证文本，用来确认异常发生时缓冲会被强制刷新而不是丢弃。"
        calls = {"n": 0}

        def _raise_after_first(db, stream_id, **kwargs):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("boom")

        with patch("app.domains.conversation.stream_runtime.raise_if_stream_cancelled", side_effect=_raise_after_first):
            with self.assertRaises(RuntimeError):
                async for _ in emit_text_stream(self.db, self.message, text, stream_id=None):
                    pass

        self.assertEqual(self._persisted_text(), text[:12])

    async def test_act_step_flushes_pending_text_on_model_error(self) -> None:
        error = RuntimeError("model stream broken")
        with (
            patch("app.domains.conversation.agent_service.MemoryService.retrieve_context", return_value=SimpleNamespace(summary="", items=[])),
            patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[]),
            patch("app.agent.llm.create_chat_model", return_value=_FailingStreamModel("部分回答内容", error)),
        ):
            with self.assertRaises(RuntimeError):
                await act_step(
                    self.db,
                    SimpleNamespace(id=self.thread_id, user_id=self.user_id),
                    self.message,
                    SimpleNamespace(),
                    state={"user_message": "你好", "reasoning_steps": [], "stream_id": None, "plan": ""},
                    emit=lambda _event: None,
                )

        self.assertEqual(self._persisted_text(), "部分回答内容")

    async def test_act_step_injects_plan_into_model_context(self) -> None:
        captured: list = []

        class _CapturingModel:
            def bind_tools(self, tools):
                return self

            async def astream(self, messages):
                from langchain_core.messages import AIMessageChunk

                captured.append(messages)
                yield AIMessageChunk(content="好的")

        with (
            patch("app.domains.conversation.agent_service.MemoryService.retrieve_context", return_value=SimpleNamespace(summary="", items=[])),
            patch("app.domains.conversation.agent_service.build_agent_tools", new_callable=AsyncMock, return_value=[]),
            patch("app.agent.llm.create_chat_model", return_value=_CapturingModel()),
        ):
            await act_step(
                self.db,
                SimpleNamespace(id=self.thread_id, user_id=self.user_id),
                self.message,
                SimpleNamespace(),
                state={"user_message": "帮我安排一下", "reasoning_steps": [], "stream_id": None, "plan": "整理日程要点"},
                emit=lambda _event: None,
            )

        from app.agent import llm as llm_module

        final_prompt = llm_module.extract_message_text(captured[0][-1])
        self.assertIn("本轮执行计划：整理日程要点", final_prompt)


if __name__ == "__main__":
    unittest.main()
