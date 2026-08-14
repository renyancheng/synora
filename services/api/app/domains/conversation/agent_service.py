"""Agent 执行服务：general_chat 的 plan → act → observe → reflect 主循环。

legacy 路径（``_consume_stream_legacy``）与 LangGraph 节点（``app.agent.nodes``）
共用本模块的公开步骤函数；节点通过 ``get_stream_writer()`` 传入 emit，步骤以
持久化 dict 返回、SSE 事件由 emit 实时写出，checkpoint 不保存 ORM 实例。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator, Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import llm
from app.agent.tools import build_agent_tools
from app.config import get_settings
from app.domains.conversation.stream_runtime import (
    MessageTextBuffer,
    build_reasoning_step_event,
    finish_tool_audit,
    raise_if_stream_cancelled,
    serialize_any,
    start_tool_audit,
)
from app.domains.memory.service import MemoryService
from app.models import AgentRun, AgentToolCallAudit, ConversationMessage, ConversationThread
from app.runtime.context_assembler import ContextAssembler
from app.security import mint_token

logger = logging.getLogger(__name__)

# general_chat 分支不注入的 intake 写工具：避免 LLM 自主调用这些工具产出
# 无卡片、无 pending 的“伪草稿”，保证日程/速记创建统一走 intake 节点。
GENERAL_CHAT_EXCLUDED_TOOLS = {
    "parse_schedule_draft",
    "detect_schedule_conflicts",
    "create_schedule_after_approval",
    "prepare_quick_note_draft",
    "create_quick_note_after_approval",
}

# general_chat 分支注入的只读工具白名单：时间查询 + 联网搜索。白名单注入 +
# 观察阶段同样白名单匹配，模型只能调用这两个真实存在的原生工具，
# 不会产生“未知工具”空转；intake 写工具依旧完全排除。
GENERAL_CHAT_ACTIVE_TOOLS = {"get_current_time", "web_search"}

RECENT_MESSAGE_DB_WINDOW = 12
RECENT_MESSAGE_LLM_WINDOW = 8

# 确定性 plan 阈值：简单问答（无工具倾向、无时间/日程/速记锚点、较短输入）
# 直接采用确定性计划，不再发起额外 LLM 调用。
DETERMINISTIC_PLAN_MAX_INPUT_CHARS = 40


def _extract_model_chunk_delta(chunk: Any) -> str:
    """从流式 AIMessageChunk 提取纯文本增量（兼容 str 与 list[dict] content）。"""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "")
                if text:
                    parts.append(text)
        return "".join(parts)
    return ""


def make_aimessage(content: str, tool_calls: list[dict] | None) -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=[
            {"name": tc.get("name", ""), "args": tc.get("args", {}), "id": tc.get("id", ""), "type": "tool_call"}
            for tc in tool_calls or []
        ],
    )


def serialize_tool_calls(tool_calls: list[dict] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tc in tool_calls or []:
        name = str(tc.get("name") or "").strip()
        if not name:
            # 丢弃名称为空的残缺调用，避免 observe 阶段“未知工具：”空转。
            continue
        args = tc.get("args") or {}
        if isinstance(args, str):
            # 部分增量累积出的 JSON 字符串，尝试还原为对象。
            try:
                parsed = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                parsed = {}
            args = parsed if isinstance(parsed, dict) else {}
        result.append({"name": name, "args": args, "id": str(tc.get("id") or "")})
    return result


def _merge_streamed_tool_calls(chunk: Any, accumulated: list[dict]) -> list[dict]:
    """合并流式工具调用：完整列表按位置合并，部分增量按 index 累积。

    部分提供方（如 dashscope 兼容模式）在真实调用之后会追加若干
    ``name=''`` 的空名占位条目：这些条目必须忽略而不是覆盖已捕获的调用，
    否则 observe 阶段会误判为“未知工具”空转或丢失工具调用。
    """
    calls = list(getattr(chunk, "tool_calls", None) or [])
    for position, call in enumerate(calls):
        name = str((call.get("name") if isinstance(call, dict) else getattr(call, "name", "")) or "").strip()
        if not name:
            # 空名占位条目：跳过，不覆盖已捕获的调用。
            continue
        while len(accumulated) <= position:
            accumulated.append({"name": "", "args": "", "id": ""})
        target = accumulated[position]
        target["name"] = name
        args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
        if args:
            target["args"] = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
        call_id = str((call.get("id") if isinstance(call, dict) else getattr(call, "id", "")) or "")
        if call_id:
            target["id"] = call_id
    if calls:
        return accumulated
    partials = list(getattr(chunk, "tool_call_chunks", None) or [])
    for part in partials:
        index = int(getattr(part, "index", 0) or 0)
        while len(accumulated) <= index:
            accumulated.append({"name": "", "args": "", "id": ""})
        target = accumulated[index]
        name = str(getattr(part, "name", "") or "").strip()
        if name:
            target["name"] = name
        args = str(getattr(part, "args", "") or "")
        if args:
            # args 以 JSON 字符串分片增量下发，按片段累积。
            target["args"] = f"{target['args']}{args}"
        call_id = str(getattr(part, "id", "") or "")
        if call_id:
            target["id"] = call_id
    return accumulated


def serialize_aimessage(message: Any) -> dict[str, Any]:
    return {"role": "ai", "content": llm.extract_message_text(message), "tool_calls": serialize_tool_calls(getattr(message, "tool_calls", None) or [])}


def deserialize_message(item: dict[str, Any]) -> Any:
    role = str(item.get("role") or "")
    content = str(item.get("content") or "")
    if role == "ai":
        return make_aimessage(content, list(item.get("tool_calls") or []))
    if role == "tool":
        return ToolMessage(content=content, tool_call_id=str(item.get("tool_call_id") or ""), name=str(item.get("name") or ""))
    return HumanMessage(content=content)


def serialize_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        return str(result)


def sanitize_follow_up_prompt(value: Any) -> str | None:
    prompt = re.sub(r"\s+", " ", str(value or "")).strip()
    if not prompt:
        return None
    prompt = re.sub(r"(?i)(api[_ -]?key|authorization|bearer|token|password)\s*[:=]\s*\S+", "[已省略]", prompt)
    return prompt[:240]


def extract_langchain_delta(event: dict[str, Any]) -> str:
    chunk = event.get("data", {}).get("chunk")
    if chunk is None:
        return ""
    return llm.extract_message_text(chunk)


def extract_langchain_final_text(event: dict[str, Any]) -> str:
    output = event.get("data", {}).get("output")
    if isinstance(output, dict):
        messages = output.get("messages")
        if isinstance(messages, list) and messages:
            return llm.extract_message_text(messages[-1])
        if isinstance(output.get("output"), str):
            return str(output.get("output")).strip()
        return ""
    if isinstance(output, list) and output:
        return llm.extract_message_text(output[-1])
    return llm.extract_message_text(output)


def build_general_chat_messages(
    db: Session,
    thread: ConversationThread,
    assistant_message: ConversationMessage,
    *,
    user_message: str,
    attachment_parts: list[dict],
    conversation_history_lines: list[str] | None = None,
    agent_messages: list[dict] | None = None,
    follow_up_prompt: str | None = None,
    plan: str | None = None,
) -> list[Any]:
    recent_messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == thread.id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(RECENT_MESSAGE_DB_WINDOW)
    ).all()
    ordered = list(reversed(list(recent_messages)))
    memory_context = MemoryService().retrieve_context(db, user_id=thread.user_id, query_text=user_message)
    memory_text = ContextAssembler.build_memory_context(memory_summary=memory_context.summary, memory_items=memory_context.items)
    history_text = ContextAssembler.build_conversation_history_context(conversation_history_lines)
    prompt_parts = [part for part in (memory_text, history_text, f"当前输入：\n{user_message}".strip()) if part]
    # plan 进入模型上下文，约束当前执行目标；plan 只用于展示的伪计划已废弃。
    if plan and str(plan).strip():
        prompt_parts.append(f"本轮执行计划：{str(plan).strip()}。请只围绕该目标行动，不要偏离。")
    messages = llm.build_langchain_messages(
        recent_messages=[{"role": item.role, "content": item.text_content or ""} for item in ordered[-RECENT_MESSAGE_LLM_WINDOW:] if item.id != assistant_message.id and item.text_content],
        user_message="\n\n".join(prompt_parts).strip(),
        attachment_parts=attachment_parts,
    )
    for item in agent_messages or []:
        messages.append(deserialize_message(item))
    if follow_up_prompt:
        messages.append(HumanMessage(content=str(follow_up_prompt)))
    return messages


async def stream_general_chat(
    db: Session,
    thread: ConversationThread,
    assistant_message: ConversationMessage,
    agent_run: AgentRun,
    *,
    user_message: str,
    attachment_parts: list[dict],
    conversation_history_lines: list[str] | None = None,
    stream_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    recent_messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == thread.id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(RECENT_MESSAGE_DB_WINDOW)
    ).all()
    ordered = list(reversed(list(recent_messages)))
    tools = await build_agent_tools(exclude_names=GENERAL_CHAT_EXCLUDED_TOOLS)
    agent = llm.build_general_chat_agent(get_settings(), tools)
    memory_context = MemoryService().retrieve_context(
        db,
        user_id=thread.user_id,
        query_text=user_message,
    )
    memory_text = ContextAssembler.build_memory_context(
        memory_summary=memory_context.summary,
        memory_items=memory_context.items,
    )
    history_text = ContextAssembler.build_conversation_history_context(conversation_history_lines)
    prompt_parts: list[str] = []
    if memory_text:
        prompt_parts.append(memory_text)
    if history_text:
        prompt_parts.append(history_text)
    prompt_parts.append(f"当前输入：\n{user_message}".strip())
    messages = llm.build_langchain_messages(
        recent_messages=[
            {"role": item.role, "content": item.text_content or ""}
            for item in ordered[-RECENT_MESSAGE_LLM_WINDOW:]
            if item.id != assistant_message.id and item.text_content
        ],
        user_message="\n\n".join(part for part in prompt_parts if part).strip(),
        attachment_parts=attachment_parts,
    )

    final_text = assistant_message.text_content or ""
    tool_audits: dict[str, AgentToolCallAudit] = {}
    text_buffer = MessageTextBuffer(db, assistant_message)
    try:
        async for event in agent.astream_events({"messages": messages}, version="v2"):
            raise_if_stream_cancelled(db, stream_id)
            event_name = str(event.get("event") or "")
            if event_name == "on_chat_model_stream":
                delta = extract_langchain_delta(event)
                if not delta:
                    continue
                final_text += delta
                text_buffer.append(delta)
                if text_buffer.needs_flush():
                    text_buffer.flush()
                yield {"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": delta}}
                continue

            if event_name == "on_tool_start":
                tool_name = str(event.get("name") or "tool")
                call_id = str(event.get("run_id") or mint_token())
                tool_audits[call_id] = start_tool_audit(
                    db,
                    agent_run_id=agent_run.id,
                    tool_name=tool_name,
                    request_json={"input": event.get("data", {}).get("input")},
                )
                yield {
                    "event": "tool_call_started",
                    "data": {"tool_name": tool_name, "call_id": call_id},
                }
                continue

            if event_name == "on_tool_end":
                tool_name = str(event.get("name") or "tool")
                call_id = str(event.get("run_id") or "")
                audit = tool_audits.get(call_id)
                finish_tool_audit(
                    db,
                    audit,
                    status="ok",
                    response_json={"output": serialize_any(event.get("data", {}).get("output"))},
                )
                yield {
                    "event": "tool_call_completed",
                    "data": {"tool_name": tool_name, "call_id": call_id},
                }
                continue

            if event_name == "on_tool_error":
                tool_name = str(event.get("name") or "tool")
                call_id = str(event.get("run_id") or "")
                audit = tool_audits.get(call_id)
                message = str(event.get("data", {}).get("error") or "Tool call failed")
                finish_tool_audit(
                    db,
                    audit,
                    status="failed",
                    response_json={},
                    error_message=message,
                )
                yield {
                    "event": "tool_call_failed",
                    "data": {"tool_name": tool_name, "call_id": call_id, "message": message},
                }
                continue

            if event_name == "on_chain_end":
                tail = extract_langchain_final_text(event)
                if tail:
                    final_text = tail
                    text_buffer.set_text(final_text)
                    text_buffer.flush()
    finally:
        # 完成 / 取消 / 失败统一强制刷新，避免 DB 文本落后于客户端已显示文本。
        text_buffer.flush()


def _deterministic_plan(state: dict[str, Any]) -> str:
    """简单问答的确定性计划：无需额外 LLM 调用。

    命中条件：无工具倾向输入且足够短（避免误伤需要工具或多轮动作的请求），
    计划直接以“回答用户问题”约束 act 的模型上下文。
    """
    user_message = str(state.get("user_message") or "").strip()
    if len(user_message) > DETERMINISTIC_PLAN_MAX_INPUT_CHARS:
        return ""
    hints = ("几点", "星期", "日期", "现在时间", "今天几号", "天气", "计算", "翻译", "多少", "什么是", "为什么", "怎么做")
    if not any(hint in user_message for hint in hints):
        return ""
    return "回答用户问题"


async def plan_step(db: Session, thread: ConversationThread, assistant_message: ConversationMessage, agent_run: AgentRun, *, state: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    del db, thread, agent_run
    plan = _deterministic_plan(state)
    plan_source = "deterministic" if plan else "llm"
    degraded = False
    if not plan:
        try:
            result = await llm.ainvoke_structured(
                get_settings(),
                schema=llm.PlanResult,
                system_prompt=(
                    "你是 Synora 的规划助手。用一句话（不超过20字）描述当前回合你要执行的核心动作，"
                    "面向用户可读。若只是简单问答，直接给出回答意图。该计划会进入执行上下文约束本轮行动。"
                    "计划只针对当前输入，不得复述或延续上一轮的回答。"
                ),
                user_text=state.get("user_message") or "",
                operation="agent_plan",
            )
            plan = str(result.plan or "").strip() or "回答用户"
        except Exception as exc:
            # 降级路径：计划生成失败采用确定性兜底，不阻断主流程；结构化记录降级。
            plan = "回答用户"
            degraded = True
            logger.warning(
                "agent_step_degraded run_id=%s conversation_id=%s operation=agent_plan reason=%s fallback=deterministic",
                state.get("agent_run_id"),
                state.get("conversation_id"),
                type(exc).__name__,
            )
    step = {
        "seq": len(list(state.get("reasoning_steps") or [])) + 1,
        "step_type": "plan",
        "label": "规划",
        "content": plan,
        "status": "completed",
        "iteration": 0,
        "plan_source": plan_source,
        "degraded": degraded,
    }
    emit(build_reasoning_step_event(assistant_message.id, step))
    return {"plan": plan, "steps": [step]}


async def act_step(db: Session, thread: ConversationThread, assistant_message: ConversationMessage, agent_run: AgentRun, *, state: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    del agent_run
    iteration = int(state.get("iteration_count") or 0)
    running_step = {"seq": len(list(state.get("reasoning_steps") or [])) + 1, "step_type": "act", "label": "行动", "content": "", "status": "running", "iteration": iteration}
    emit(build_reasoning_step_event(assistant_message.id, running_step))
    execution_plan = str(state.get("plan") or "").strip() if iteration == 0 else ""
    messages = build_general_chat_messages(
        db,
        thread,
        assistant_message,
        user_message=state.get("user_message") or "",
        attachment_parts=list(state.get("attachment_parts") or []),
        conversation_history_lines=list(state.get("conversation_history_lines") or []),
        agent_messages=list(state.get("agent_messages") or []),
        follow_up_prompt=state.get("follow_up_prompt"),
        plan=execution_plan or None,
    )
    model = llm.create_chat_model(get_settings(), temperature=0.6, streaming=True, enable_thinking=False)
    act_tools = await build_agent_tools(include_names=GENERAL_CHAT_ACTIVE_TOOLS)
    bound_model = model.bind_tools(act_tools) if act_tools else model
    # 反重复重跑：清空上一轮完全重复的文本，先通知前端重置，再流式输出新回答。
    anti_repeat = bool(state.get("anti_repeat_used"))
    if anti_repeat:
        assistant_message.text_content = ""
        db.commit()
        emit({"event": "message_reset", "data": {"assistant_message_id": assistant_message.id}})
    final_text, iteration_text, raw_tool_calls = assistant_message.text_content or "", "", []
    text_buffer = MessageTextBuffer(db, assistant_message)
    try:
        async for chunk in bound_model.astream(messages):
            raise_if_stream_cancelled(db, state.get("stream_id"))
            delta = _extract_model_chunk_delta(chunk)
            if delta:
                iteration_text += delta
                final_text += delta
                text_buffer.append(delta)
                if text_buffer.needs_flush():
                    text_buffer.flush()
                emit({"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": delta}})
            # 流式工具调用按完整列表或部分增量（tool_call_chunks）合并累积，
            # 避免名称为空的残缺调用被当成“未知工具”空转。
            raw_tool_calls = _merge_streamed_tool_calls(chunk, raw_tool_calls)
    finally:
        # 完成 / 取消 / 失败统一强制刷新，避免 DB 文本落后于客户端已显示文本。
        text_buffer.flush()
    # args 经序列化归一为 dict（合并缓冲中可能是 JSON 字符串或空串），
    # 避免 AIMessage 的 pydantic 校验失败。
    normalized_tool_calls = serialize_tool_calls(raw_tool_calls)
    aimessage = make_aimessage(iteration_text, normalized_tool_calls)
    completed_step = {**running_step, "content": iteration_text or "（无文本输出）", "status": "completed"}
    emit(build_reasoning_step_event(assistant_message.id, completed_step))
    return {"aimessage": serialize_aimessage(aimessage), "pending_tool_calls": normalized_tool_calls, "iteration": iteration + 1, "steps": [completed_step]}


async def observe_step(db: Session, thread: ConversationThread, assistant_message: ConversationMessage, agent_run: AgentRun, *, state: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    del thread
    tool_calls = list(state.get("pending_tool_calls") or [])
    running_step = {"seq": len(list(state.get("reasoning_steps") or [])) + 1, "step_type": "observe", "label": "观察", "content": "", "status": "running", "iteration": int(state.get("iteration_count") or 0)}
    emit(build_reasoning_step_event(assistant_message.id, running_step))
    if not tool_calls:
        completed_step = {**running_step, "content": "本轮无工具调用", "status": "completed"}
        emit(build_reasoning_step_event(assistant_message.id, completed_step))
        return {"tool_messages": [], "observation": "本轮无工具调用", "tool_failed": False, "pending_tool_calls": [], "steps": [completed_step]}
    tool_map = {tool.name: tool for tool in await build_agent_tools(include_names=GENERAL_CHAT_ACTIVE_TOOLS)}
    tool_messages: list[dict[str, Any]] = []
    summaries: list[str] = []
    tool_failed = False
    for call in tool_calls:
        raise_if_stream_cancelled(db, state.get("stream_id"), force_database_check=True)
        name = str(call.get("name") or "").strip()
        args, call_id = call.get("args") or {}, str(call.get("id") or mint_token())
        tool = tool_map.get(name)
        if not name:
            # 名称为空的残缺调用：直接按失败收口，不进入“未知工具”空转。
            tool_failed = True
            content_text = "模型尝试调用工具但未给出工具名，已忽略。"
            emit({"event": "tool_call_failed", "data": {"tool_name": "", "call_id": call_id, "message": content_text}})
            tool_messages.append({"role": "tool", "content": content_text, "name": "", "tool_call_id": call_id})
            summaries.append(f"{content_text[:80]}")
            continue
        emit({"event": "tool_call_started", "data": {"tool_name": name, "call_id": call_id}})
        audit = start_tool_audit(db, agent_run_id=agent_run.id, tool_name=name, request_json={"arguments": args})
        if tool is None:
            tool_failed, content_text = True, f"未知工具：{name}"
            finish_tool_audit(db, audit, status="failed", response_json={}, error_message=content_text)
            emit({"event": "tool_call_failed", "data": {"tool_name": name, "call_id": call_id, "message": content_text}})
        else:
            try:
                result = await tool.ainvoke(args)
                content_text = serialize_tool_result(result)
                finish_tool_audit(db, audit, status="ok", response_json=serialize_any(result))
                emit({"event": "tool_call_completed", "data": {"tool_name": name, "call_id": call_id}})
            except Exception as exc:
                tool_failed, content_text = True, f"工具执行失败：{exc}"
                finish_tool_audit(db, audit, status="failed", response_json={}, error_message=str(exc))
                emit({"event": "tool_call_failed", "data": {"tool_name": name, "call_id": call_id, "message": str(exc)}})
        tool_messages.append({"role": "tool", "content": content_text, "name": name, "tool_call_id": call_id})
        summaries.append(f"{name}: {content_text[:80]}")
    observation = "；".join(summaries)[:120] or "本轮无工具调用"
    completed_step = {**running_step, "content": observation, "status": "completed"}
    emit(build_reasoning_step_event(assistant_message.id, completed_step))
    return {"tool_messages": tool_messages, "observation": observation, "tool_failed": tool_failed, "pending_tool_calls": [], "steps": [completed_step]}


async def reflect_step(db: Session, thread: ConversationThread, assistant_message: ConversationMessage, agent_run: AgentRun, *, state: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    del thread, agent_run
    settings = get_settings()
    iteration = int(state.get("iteration_count") or 0)
    max_iter = int(state.get("max_iterations") or settings.agent_max_loop_iterations)
    aimessage = state.get("current_aimessage") or {}
    had_tool_calls = bool(aimessage.get("tool_calls"))
    observation = str(state.get("observation") or "")
    assistant_output = str(aimessage.get("content") or assistant_message.text_content or "").strip()
    tool_failed = bool(state.get("tool_failed"))
    decision, rationale, follow_up_prompt = "done", "", None
    degraded = False
    anti_repeat_triggered = False
    empty_retries = int(state.get("anti_empty_retries") or 0)
    if not assistant_output and not had_tool_calls:
        if empty_retries < 2 and iteration < max_iter:
            # 空回答护栏：模型未生成任何文本且未调用工具（qwen 间歇性空流）时
            # 注入指令重跑，最多 2 次，避免“空气泡”直接收口。
            decision = "continue"
            rationale = "模型未生成任何回答文本，要求重新作答"
            follow_up_prompt = "你刚才没有生成任何回答文本。请忽略历史，直接针对当前输入给出简短回答，必须输出非空文本。"
            empty_retries += 1
        else:
            rationale = "多次重试仍未生成回答文本，采用兜底文案收口"
            degraded = True
    elif (
        not state.get("anti_repeat_used")
        and assistant_output
        and _repeats_previous_answer(db, int(state.get("conversation_id") or 0), assistant_message.id, assistant_output)
    ):
        # 防粘滞护栏：本轮回答与上一轮完全重复，注入指令重跑一轮（单次，防死循环）。
        decision = "continue"
        rationale = "检测到与上一轮完全重复的回答，要求重新作答"
        follow_up_prompt = "你刚才的回答与上一轮完全重复。请忽略历史回答，只针对当前输入重新作答，不要复述任何固定话术。"
        anti_repeat_triggered = True
    elif not had_tool_calls:
        rationale = "本轮无工具调用，回答完整"
    elif iteration >= max_iter:
        rationale = f"已达最大迭代次数（{max_iter}）"
    elif tool_failed:
        rationale = "工具执行失败，避免重复调用"
    elif assistant_output:
        rationale = "工具已返回且已生成用户可读回答"
    else:
        try:
            evidence = {"user_goal": str(state.get("user_message") or "")[:1200], "plan": str(state.get("plan") or "")[:600], "iteration": iteration, "max_iterations": max_iter, "current_assistant_output": assistant_output[:2000], "observation": observation[:1600], "tool_failed": tool_failed, "tool_messages": [{"name": str(item.get("name") or "")[:120], "content": str(item.get("content") or "")[:1200]} for item in list(state.get("agent_messages") or []) if item.get("role") == "tool"][-4:]}
            result = await llm.ainvoke_structured(settings, schema=llm.ReflectDecision, system_prompt="你是 Synora 的执行评估器。根据提供的执行证据判断是否需要下一轮行动。只有当前没有面向用户的回答且工具结果明确不足时才令 is_complete=false。follow_up_prompt 只能是给模型的简短任务指引，不得包含密钥、令牌、完整附件、用户隐私原文或工具内部错误详情。", user_text=json.dumps(evidence, ensure_ascii=False), operation="agent_reflect")
            if result.is_complete:
                rationale = result.rationale or "信息已充分"
            else:
                decision, rationale = "continue", result.rationale or "需要继续行动"
                follow_up_prompt = sanitize_follow_up_prompt(result.follow_up_prompt)
                if not follow_up_prompt:
                    decision, rationale = "done", "缺少可执行的下一步指引，保守收尾"
        except Exception as exc:
            # 降级路径：评估失败采用保守收尾，不阻断主流程；结构化记录降级。
            rationale = "评估失败，保守收尾"
            degraded = True
            logger.warning(
                "agent_step_degraded run_id=%s conversation_id=%s operation=agent_reflect reason=%s fallback=conservative_close",
                state.get("agent_run_id"),
                state.get("conversation_id"),
                type(exc).__name__,
            )
    step = {"seq": len(list(state.get("reasoning_steps") or [])) + 1, "step_type": "reflect", "label": "反思", "content": rationale or decision, "status": "completed", "iteration": iteration, "degraded": degraded}
    emit(build_reasoning_step_event(assistant_message.id, step))
    return {
        "loop_decision": decision,
        "reflection": rationale,
        "follow_up_prompt": follow_up_prompt,
        "steps": [step],
        "anti_repeat_used": bool(state.get("anti_repeat_used")) or anti_repeat_triggered,
        "anti_empty_retries": empty_retries,
    }


def _normalize_answer_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _repeats_previous_answer(db: Session, conversation_id: int, current_message_id: int, current_text: str) -> bool:
    """本轮回答是否与库中上一条 assistant 文本消息完全一致（防粘滞护栏）。"""
    normalized = _normalize_answer_text(current_text)
    if not normalized or not conversation_id:
        return False
    previous = db.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "assistant",
            ConversationMessage.message_type == "text",
            ConversationMessage.id != current_message_id,
        )
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(1)
    )
    if previous is None:
        return False
    return _normalize_answer_text(str(previous.text_content or "")) == normalized
