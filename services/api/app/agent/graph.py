"""LangGraph 图组装。

intent_router 依据 LLM 意图路由到 general_chat / schedule_intake /
quick_note_intake 分支，统一收口到 finalize。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, StateGraph

from app.agent.checkpointer import get_checkpointer_sync
from app.agent.nodes import (
    finalize_node,
    general_chat_node,
    quick_note_intake_node,
    route_intent,
    schedule_intake_node,
)
from app.agent.state import AgentState


def _route_branch(state: AgentState) -> str:
    return state.get("intent") or ""


@lru_cache
def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("intent_router", route_intent)
    graph.add_node("general_chat", general_chat_node)
    graph.add_node("schedule_intake", schedule_intake_node)
    graph.add_node("quick_note_intake", quick_note_intake_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("intent_router")
    graph.add_conditional_edges(
        "intent_router",
        _route_branch,
        {
            "general_chat": "general_chat",
            "schedule_intake": "schedule_intake",
            "quick_note_intake": "quick_note_intake",
        },
    )
    for name in ("general_chat", "schedule_intake", "quick_note_intake"):
        graph.add_edge(name, "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=get_checkpointer_sync())
