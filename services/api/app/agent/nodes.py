"""LangGraph 节点函数。

节点通过 ``get_config()["configurable"]`` 取 ORM 对象（db / thread / agent_run /
assistant_message），通过 ``get_stream_writer()`` 实时写 SSE 事件。业务处理
复用 ``app.domains.conversation.service`` 的既有实现（局部 import 打破模块级
循环依赖），保证卡片、审批、审计与文本分块逻辑与旧路径完全一致。
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_config, get_stream_writer

from app.agent import llm
from app.agent.state import AgentState
from app.config import get_settings


def _config_items() -> dict[str, Any]:
    return get_config()["configurable"]


async def route_intent(state: AgentState) -> dict[str, Any]:
    from app.domains.conversation.service import _get_pending_state, _prepare_pending_regeneration, _resolve_contextual_draft_followup

    cfg = _config_items()
    db = cfg["db"]
    agent_run = cfg["agent_run"]
    user_id = int(state["user_id"])
    conversation_id = int(state["conversation_id"])

    text_content = state.get("user_message") or ""
    attachment_ids = list(state.get("attachment_ids") or [])
    attachment_parts = list(state.get("attachment_parts") or [])
    context = dict(state.get("context") or {})
    selected_tool = state.get("selected_tool")

    pending = _get_pending_state(db, conversation_id)
    if pending:
        intent, text_content, attachment_ids, attachment_parts, context = _prepare_pending_regeneration(
            db,
            user_id=user_id,
            pending=pending,
            text_content=text_content,
            attachment_ids=attachment_ids,
            attachment_parts=attachment_parts,
            context=context,
        )
    else:
        intent = await llm.aroute_conversation_intent(
            get_settings(),
            {
                "text_content": text_content,
                "attachment_ids": attachment_ids,
                "selected_tool": selected_tool,
                "context": context,
            },
            attachment_parts=attachment_parts,
        )
        if intent in {"schedule_intake", "quick_note_intake"}:
            resolved = _resolve_contextual_draft_followup(db, conversation_id, text_content, context)
            if resolved:
                intent, context = resolved

    agent_run.workflow = intent
    agent_run.output_json = {
        **dict(agent_run.output_json or {}),
        "workflow": intent,
        "model_name": get_settings().llm_model,
        "provider_name": "dashscope",
    }
    db.commit()

    return {
        "intent": intent,
        "user_message": text_content,
        "attachment_ids": attachment_ids,
        "attachment_parts": attachment_parts,
        "context": context,
    }


async def general_chat_node(state: AgentState) -> dict[str, Any]:
    from app.domains.conversation.service import _stream_general_chat

    cfg = _config_items()
    writer = get_stream_writer()
    async for sse in _stream_general_chat(
        cfg["db"],
        cfg["thread"],
        cfg["assistant_message"],
        cfg["agent_run"],
        user_message=state.get("user_message") or "",
        attachment_parts=list(state.get("attachment_parts") or []),
        conversation_history_lines=list(state.get("conversation_history_lines") or []),
    ):
        writer(sse)
    return {
        "assistant_text": cfg["assistant_message"].text_content or "",
        "created_message_ids": [],
        "requires_approval": None,
    }


async def schedule_intake_node(state: AgentState) -> dict[str, Any]:
    from app.domains.conversation.service import _emit_text_stream, _process_schedule_intake

    cfg = _config_items()
    writer = get_stream_writer()
    context = dict(state.get("context") or {})
    final_text, created_ids, requires_approval, tool_events = await _process_schedule_intake(
        cfg["db"],
        int(state["user_id"]),
        cfg["thread"],
        cfg["agent_run"],
        text_content=state.get("user_message") or "",
        attachment_ids=list(state.get("attachment_ids") or []),
        context=context,
        action_group_id=context.get("pending_action_group_id") or None,
        revision=int(context.get("pending_revision") or 1),
    )
    for tool_event in tool_events:
        writer(tool_event)
    async for sse in _emit_text_stream(cfg["db"], cfg["assistant_message"], final_text):
        writer(sse)
    return {
        "assistant_text": cfg["assistant_message"].text_content or "",
        "created_message_ids": created_ids,
        "requires_approval": requires_approval,
    }


async def quick_note_intake_node(state: AgentState) -> dict[str, Any]:
    from app.domains.conversation.service import _emit_text_stream, _process_quick_note_intake

    cfg = _config_items()
    writer = get_stream_writer()
    context = dict(state.get("context") or {})
    final_text, created_ids, requires_approval, tool_events = await _process_quick_note_intake(
        cfg["db"],
        int(state["user_id"]),
        cfg["thread"],
        cfg["agent_run"],
        text_content=state.get("user_message") or "",
        attachment_ids=list(state.get("attachment_ids") or []),
        context=context,
        action_group_id=context.get("pending_action_group_id") or None,
        revision=int(context.get("pending_revision") or 1),
    )
    for tool_event in tool_events:
        writer(tool_event)
    async for sse in _emit_text_stream(cfg["db"], cfg["assistant_message"], final_text):
        writer(sse)
    return {
        "assistant_text": cfg["assistant_message"].text_content or "",
        "created_message_ids": created_ids,
        "requires_approval": requires_approval,
    }


def finalize_node(state: AgentState) -> dict[str, Any]:
    """输出归一化：字段已由各分支节点写入，此处仅保证 key 存在。"""
    return {
        "assistant_text": state.get("assistant_text") or "",
        "created_message_ids": list(state.get("created_message_ids") or []),
        "requires_approval": state.get("requires_approval"),
    }
