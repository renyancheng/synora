"""LangGraph checkpointer 生命周期测试（SQLite 内存隔离）。"""

from __future__ import annotations

import unittest
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.agent.checkpointer import delete_checkpoint, get_checkpointer, reset_checkpointer
from app.config import get_settings


class _CounterState(TypedDict, total=False):
    value: int


def _increment(state: _CounterState) -> dict:
    return {"value": int(state.get("value") or 0) + 1}


def _thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


class AgentCheckpointerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._prev_path = get_settings().langgraph_checkpoint_sqlite_path
        get_settings().langgraph_checkpoint_sqlite_path = ":memory:"
        reset_checkpointer()

    async def asyncTearDown(self) -> None:
        get_settings().langgraph_checkpoint_sqlite_path = self._prev_path
        reset_checkpointer()

    async def _run_once(self, thread_id: str, initial_value: int = 0) -> int:
        checkpointer = await get_checkpointer()
        graph = StateGraph(_CounterState)
        graph.add_node("increment", _increment)
        graph.set_entry_point("increment")
        graph.add_edge("increment", END)
        compiled = graph.compile(checkpointer=checkpointer)
        result = await compiled.ainvoke(
            {"value": initial_value},
            config={"configurable": {"thread_id": thread_id}},
        )
        return int(result["value"])

    async def test_thread_isolation(self) -> None:
        self.assertEqual(await self._run_once("t-a"), 1)
        self.assertEqual(await self._run_once("t-b"), 1)
        checkpointer = await get_checkpointer()
        self.assertIsNotNone(await checkpointer.aget_tuple(_thread_config("t-a")))
        self.assertIsNotNone(await checkpointer.aget_tuple(_thread_config("t-b")))

    async def test_delete_checkpoint_removes_thread(self) -> None:
        await self._run_once("t-c")
        checkpointer = await get_checkpointer()
        self.assertIsNotNone(await checkpointer.aget_tuple(_thread_config("t-c")))
        await delete_checkpoint("t-c")
        self.assertIsNone(await checkpointer.aget_tuple(_thread_config("t-c")))

    async def test_delete_checkpoint_is_noop_for_missing(self) -> None:
        await delete_checkpoint("t-ghost")  # 不应抛异常

    async def test_checkpointer_singleton_survives_astream(self) -> None:
        await self._run_once("t-d")
        checkpointer = await get_checkpointer()
        self.assertIsNotNone(await checkpointer.aget_tuple(_thread_config("t-d")))


if __name__ == "__main__":
    unittest.main()
