"""LangGraph 图组装。

intent_router 依据 LLM 意图路由：general_chat 进入 plan→act→observe→reflect
显式循环（reflect 条件边决定回 act 或收口到 finalize）；schedule_intake /
quick_note_intake 保持直线卡片流程，统一收口到 finalize。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, StateGraph

from app.agent.checkpointer import get_checkpointer_sync
from app.agent.nodes import (
    act_node,
    finalize_node,
    observe_node,
    plan_node,
    quick_note_intake_node,
    reflect_node,
    route_intent,
    schedule_intake_node,
)
from app.agent.state import AgentState


def _route_branch(state: AgentState) -> str:
    return state.get("intent") or ""


def _route_loop(state: AgentState) -> str:
    """reflect 条件边：continue 且未达最大迭代次数则回 act，否则 finalize。"""
    if state.get("loop_decision") == "continue" and int(state.get("iteration_count") or 0) < int(
        state.get("max_iterations") or 4
    ):
        return "act"
    return "finalize"


@lru_cache
def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("intent_router", route_intent)
    graph.add_node("plan", plan_node)
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("schedule_intake", schedule_intake_node)
    graph.add_node("quick_note_intake", quick_note_intake_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("intent_router")
    graph.add_conditional_edges(
        "intent_router",
        _route_branch,
        {
            "general_chat": "plan",
            "schedule_intake": "schedule_intake",
            "quick_note_intake": "quick_note_intake",
        },
    )
    graph.add_edge("plan", "act")
    graph.add_edge("act", "observe")
    graph.add_edge("observe", "reflect")
    graph.add_conditional_edges(
        "reflect",
        _route_loop,
        {"act": "act", "finalize": "finalize"},
    )
    for name in ("schedule_intake", "quick_note_intake"):
        graph.add_edge(name, "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=get_checkpointer_sync())
