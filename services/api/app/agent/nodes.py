"""LangGraph 节点函数。

节点从 checkpoint state 中的稳定 ID 重新加载短生命周期数据库资源，通过
``get_stream_writer()`` 实时写 SSE 事件。业务处理复用公开的 Agent 执行服务
（``agent_service``）、intake 工作流（``intake_service``）与流运行时能力
（``stream_runtime``），避免 checkpoint/config 捕获活跃 Session 或 ORM 实例，
且不依赖 ``conversation.service`` 的私有实现。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.config import get_config, get_stream_writer

from app.agent import llm
from app.agent.state import AgentState
from app.config import get_settings
from app.db import SessionLocal
from app.domains.conversation.agent_service import act_step, observe_step, plan_step, reflect_step
from app.domains.conversation.intake_service import build_recent_history_lines, process_quick_note_intake, process_schedule_intake
from app.domains.conversation.pending_service import get_pending_state, prepare_pending_regeneration, resolve_contextual_draft_followup
from app.domains.conversation.stream_runtime import build_reasoning_step_event, emit_text_stream
from app.models import AgentRun, ConversationMessage, ConversationThread

logger = logging.getLogger(__name__)


class _NodeResources:
    def __init__(self, state: AgentState) -> None:
        self.db = SessionLocal()
        self.thread = self.db.get(ConversationThread, int(state["conversation_id"]))
        self.agent_run = self.db.get(AgentRun, int(state["agent_run_id"]))
        self.assistant_message = self.db.get(ConversationMessage, int(state["assistant_message_id"]))
        if not self.thread or not self.agent_run or not self.assistant_message:
            self.db.close()
            raise ValueError("Agent 运行资源不存在，无法恢复。")

    def close(self) -> None:
        self.db.close()


def _load_resources(state: AgentState) -> _NodeResources:
    # configurable 仅含 LangGraph 的 thread_id；资源只能由可持久化 ID 重载。
    _ = get_config()["configurable"].get("thread_id")
    return _NodeResources(state)


def _emit_intake_preamble(
    resources: _NodeResources,
    writer: Any,
    state: AgentState,
    *,
    content: str,
) -> list[dict[str, Any]]:
    """intake 前置：发一条 perceive 推理步骤（思考轨迹）+ 一句说明文字（消息气泡）。

    说明文字以 message_delta 写入 assistant_message.text_content，配合
    ``emit_text_stream`` 的追加语义，最终文案在其后追加不丢字。
    返回需要追加进 ``state.reasoning_steps`` 的持久化 step。
    """
    existing = list(state.get("reasoning_steps") or [])
    perceive_step: dict[str, Any] = {
        "seq": len(existing) + 1,
        "step_type": "perceive",
        "label": "感知",
        "content": content,
        "status": "completed",
        "iteration": 0,
    }
    writer(build_reasoning_step_event(resources.assistant_message.id, perceive_step))
    assistant_message = resources.assistant_message
    assistant_message.text_content = (assistant_message.text_content or "") + content
    resources.db.commit()
    writer(
        {
            "event": "message_delta",
            "data": {"assistant_message_id": assistant_message.id, "delta": content},
        }
    )
    return [perceive_step]


async def route_intent(state: AgentState) -> dict[str, Any]:
    resources = _load_resources(state)
    db = resources.db
    agent_run = resources.agent_run
    user_id = int(state["user_id"])
    conversation_id = int(state["conversation_id"])

    text_content = state.get("user_message") or ""
    attachment_ids = list(state.get("attachment_ids") or [])
    attachment_parts = list(state.get("attachment_parts") or [])
    context = dict(state.get("context") or {})
    selected_tool = state.get("selected_tool")

    # 近期对话窗口补充：_resolve_conversation_history_lines 只返回最近 12 条之外的
    # 旧相关历史，而路由与日程/速记提取都需要紧邻轮次（如“把刚才说的那个会议加上”）。
    # 近期行在前、旧历史行去重后追加，路由（取最近 6 条）与 intake 上下文均受益。
    recent_lines = build_recent_history_lines(db, resources.thread, agent_run.user_message_id)
    if recent_lines:
        existing_lines = list(context.get("conversation_history_lines") or [])
        old_lines = [line for line in existing_lines if line not in recent_lines]
        context["conversation_history_lines"] = recent_lines + old_lines

    try:
        pending = get_pending_state(db, conversation_id)
        if pending:
            intent, text_content, attachment_ids, attachment_parts, context = prepare_pending_regeneration(
                db, user_id=user_id, pending=pending, text_content=text_content,
                attachment_ids=attachment_ids, attachment_parts=attachment_parts, context=context,
            )
        else:
            intent = await llm.aroute_conversation_intent(
                get_settings(),
                {"text_content": text_content, "attachment_ids": attachment_ids, "selected_tool": selected_tool, "context": context},
                attachment_parts=attachment_parts,
            )
            if intent in {"schedule_intake", "quick_note_intake"}:
                resolved = resolve_contextual_draft_followup(db, conversation_id, text_content, context)
                if resolved:
                    intent, context = resolved
        agent_run.workflow = intent
        agent_run.output_json = {**dict(agent_run.output_json or {}), "workflow": intent, "model_name": get_settings().llm_model, "provider_name": "dashscope"}
        db.commit()
        logger.info(
            "agent_routing run_id=%s conversation_id=%s intent=%s pending=%s recent_history_lines=%s",
            agent_run.id, conversation_id, intent, bool(pending), len(recent_lines),
        )
        return {"intent": intent, "user_message": text_content, "attachment_ids": attachment_ids, "attachment_parts": attachment_parts, "context": context}
    finally:
        resources.close()


async def plan_node(state: AgentState) -> dict[str, Any]:
    """plan：产出行动计划（简单问答走确定性计划，不产生额外 LLM 调用）。"""
    resources = _load_resources(state)
    writer = get_stream_writer()
    try:
        result = await plan_step(resources.db, resources.thread, resources.assistant_message, resources.agent_run, state=dict(state), emit=writer)
    finally:
        resources.close()
    return {
        "plan": result.get("plan") or "",
        "run_started_at": time.monotonic(),
        "iteration_count": 0,
        "max_iterations": get_settings().agent_max_loop_iterations,
        "loop_decision": "continue",
        "reasoning_steps": list(result.get("steps") or []),
    }


async def act_node(state: AgentState) -> dict[str, Any]:
    """act：astream 流式吐文本，捕获 tool_calls（general_chat 不绑定任何工具）。

    运行时长预算：plan 写入 run_started_at 后，若距启动已超出预算则跳过 act_step，
    写入一条失败推理步骤并置 budget_exhausted，交由 reflect/路由安全收口（不抛异常）。
    """
    resources = _load_resources(state)
    writer = get_stream_writer()
    settings = get_settings()
    run_started_at = float(state.get("run_started_at") or 0.0)
    if (
        settings.agent_max_run_seconds > 0
        and run_started_at > 0
        and (time.monotonic() - run_started_at) >= settings.agent_max_run_seconds
    ):
        assistant_text = resources.assistant_message.text_content or ""
        step: dict[str, Any] = {
            "seq": len(list(state.get("reasoning_steps") or [])) + 1,
            "step_type": "act",
            "label": "行动",
            "content": "运行时长预算已用尽，中止本轮行动",
            "status": "failed",
            "iteration": int(state.get("iteration_count") or 0),
        }
        try:
            writer(build_reasoning_step_event(resources.assistant_message.id, step))
        finally:
            resources.close()
        return {
            "agent_messages": [],
            "pending_tool_calls": [],
            "current_aimessage": None,
            "iteration_count": int(state.get("iteration_count") or 0),
            "assistant_text": assistant_text,
            "budget_exhausted": True,
            "reasoning_steps": [step],
        }
    current_iteration = int(state.get("iteration_count") or 0)
    try:
        result = await act_step(resources.db, resources.thread, resources.assistant_message, resources.agent_run, state=dict(state), emit=writer)
        assistant_text = resources.assistant_message.text_content or ""
    finally:
        resources.close()
    aimessage = result.get("aimessage")
    # token 记账：本轮 round_tokens 累加进 state.total_tokens，并把本轮指标追加进 step_metrics。
    round_tokens = result.get("round_tokens") or {}
    round_prompt = int(round_tokens.get("prompt_tokens") or 0)
    round_completion = int(round_tokens.get("completion_tokens") or 0)
    round_latency = int(round_tokens.get("latency_ms") or 0)
    total_tokens = int(state.get("total_tokens") or 0) + round_prompt + round_completion
    step_metrics = list(state.get("step_metrics") or [])
    step_metrics.append(
        {
            "iteration": current_iteration,
            "step_type": "act",
            "prompt_tokens": round_prompt,
            "completion_tokens": round_completion,
            "latency_ms": round_latency,
        }
    )
    return {
        "agent_messages": [aimessage] if aimessage else [],
        "pending_tool_calls": list(result.get("pending_tool_calls") or []),
        "current_aimessage": aimessage,
        "iteration_count": int(result.get("iteration") or 1),
        "assistant_text": assistant_text,
        "reasoning_steps": list(result.get("steps") or []),
        "memory_payload": result.get("memory_payload") or {"summary": "", "items": []},
        "total_tokens": total_tokens,
        "step_metrics": step_metrics,
    }


async def observe_node(state: AgentState) -> dict[str, Any]:
    """observe：执行 tool_calls，产出 ToolMessage 与观察摘要（0 次 LLM）。"""
    resources = _load_resources(state)
    writer = get_stream_writer()
    try:
        result = await observe_step(resources.db, resources.thread, resources.assistant_message, resources.agent_run, state=dict(state), emit=writer)
    finally:
        resources.close()
    return {
        "agent_messages": list(result.get("tool_messages") or []),
        "observation": result.get("observation") or "",
        "tool_failed": bool(result.get("tool_failed")),
        "tool_failed_all": bool(result.get("tool_failed_all")),
        "pending_tool_calls": [],
        "reasoning_steps": list(result.get("steps") or []),
    }


async def reflect_node(state: AgentState) -> dict[str, Any]:
    """reflect：启发式短路 + LLM 评估，产出 loop_decision。

    运行时长预算：若 act 已因预算中止（budget_exhausted）且 reflect_step 仍判定
    continue，则覆盖为 done 并给出安全收口说明，避免预算中止后继续空转。
    """
    resources = _load_resources(state)
    writer = get_stream_writer()
    try:
        result = await reflect_step(resources.db, resources.thread, resources.assistant_message, resources.agent_run, state=dict(state), emit=writer)
    finally:
        resources.close()
    loop_decision = result.get("loop_decision") or "done"
    reflection = result.get("reflection") or ""
    if state.get("budget_exhausted") and loop_decision == "continue":
        loop_decision = "done"
        reflection = "已达运行时长预算，安全收口"
    return {
        "loop_decision": loop_decision,
        "reflection": reflection,
        "follow_up_prompt": result.get("follow_up_prompt"),
        "anti_repeat_used": bool(result.get("anti_repeat_used")),
        "anti_empty_retries": int(result.get("anti_empty_retries") or 0),
        "anti_commitment_used": bool(result.get("anti_commitment_used")),
        "reasoning_steps": list(result.get("steps") or []),
    }


async def schedule_intake_node(state: AgentState) -> dict[str, Any]:
    resources = _load_resources(state)
    writer = get_stream_writer()
    context = dict(state.get("context") or {})
    try:
        preamble_steps = _emit_intake_preamble(resources, writer, state, content="我注意到你想安排日程，我来整理一下。")
        final_text, created_ids, requires_approval, tool_events = await process_schedule_intake(
            resources.db, int(state["user_id"]), resources.thread, resources.agent_run,
            text_content=state.get("user_message") or "", attachment_ids=list(state.get("attachment_ids") or []),
            context=context, action_group_id=context.get("pending_action_group_id") or None,
            revision=int(context.get("pending_revision") or 1), stream_id=state.get("stream_id"),
        )
        for tool_event in tool_events:
            writer(tool_event)
        async for sse in emit_text_stream(resources.db, resources.assistant_message, final_text, stream_id=state.get("stream_id")):
            writer(sse)
        assistant_text = resources.assistant_message.text_content or ""
    finally:
        resources.close()
    return {
        "assistant_text": assistant_text,
        "created_message_ids": created_ids,
        "requires_approval": requires_approval,
        "reasoning_steps": preamble_steps,
    }


async def quick_note_intake_node(state: AgentState) -> dict[str, Any]:
    resources = _load_resources(state)
    writer = get_stream_writer()
    context = dict(state.get("context") or {})
    try:
        preamble_steps = _emit_intake_preamble(resources, writer, state, content="我帮你记一条速记。")
        final_text, created_ids, requires_approval, tool_events = await process_quick_note_intake(
            resources.db, int(state["user_id"]), resources.thread, resources.agent_run,
            text_content=state.get("user_message") or "", attachment_ids=list(state.get("attachment_ids") or []),
            context=context, action_group_id=context.get("pending_action_group_id") or None,
            revision=int(context.get("pending_revision") or 1), stream_id=state.get("stream_id"),
        )
        for tool_event in tool_events:
            writer(tool_event)
        async for sse in emit_text_stream(resources.db, resources.assistant_message, final_text, stream_id=state.get("stream_id")):
            writer(sse)
        assistant_text = resources.assistant_message.text_content or ""
    finally:
        resources.close()
    return {
        "assistant_text": assistant_text,
        "created_message_ids": created_ids,
        "requires_approval": requires_approval,
        "reasoning_steps": preamble_steps,
    }


def finalize_node(state: AgentState) -> dict[str, Any]:
    """输出归一化：字段已由各分支节点写入，此处仅保证 key 存在并透传推理轨迹。"""
    return {
        "assistant_text": state.get("assistant_text") or "",
        "created_message_ids": list(state.get("created_message_ids") or []),
        "requires_approval": state.get("requires_approval"),
        "reasoning_steps": list(state.get("reasoning_steps") or []),
    }
