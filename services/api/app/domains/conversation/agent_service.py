"""Agent 执行服务：general_chat 的 plan → act → observe → reflect 主循环。

legacy 路径（``_consume_stream_legacy``）与 LangGraph 节点（``app.agent.nodes``）
共用本模块的公开步骤函数；节点通过 ``get_stream_writer()`` 传入 emit，步骤以
持久化 dict 返回、SSE 事件由 emit 实时写出，checkpoint 不保存 ORM 实例。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
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

# general_chat 循环的系统提示词：训练知识盲区与时效性信息必须先联网搜索再回答。
GENERAL_CHAT_SYSTEM_PROMPT = (
    "你是 Synora 的私人智能助理，负责问答与信息查询。回答要自然、准确、简洁，"
    "不要伪造工具执行结果。对于训练知识无法确认的内容，或涉及时效性信息的问题"
    "（新闻、价格、行情、汇率、天气、赛事、最新进展、政策变动等），必须先调用 "
    "web_search 工具获取最新资料，再基于搜索结果回答，并在回答中注明信息来源；"
    "禁止仅凭训练知识臆测时效信息。常识性、稳定的知识问题可以直接回答，无需搜索。"
    "需要实时时间时调用 get_current_time 工具。始终只回答「当前输入」中的最新问题，"
    "历史对话仅作为背景参考，禁止重复或延续上一轮的回答。"
)

RECENT_MESSAGE_DB_WINDOW = 12
RECENT_MESSAGE_LLM_WINDOW = 8

# 确定性 plan 阈值：简单问答（无工具倾向、无时间/日程/速记锚点、较短输入）
# 直接采用确定性计划，不再发起额外 LLM 调用。
DETERMINISTIC_PLAN_MAX_INPUT_CHARS = 40

# observe 阶段真实工具并发上限（asyncio.Semaphore），避免多工具同时打爆下游。
GENERAL_CHAT_TOOL_CONCURRENCY = 4

# 工具结果写入 ToolMessage 前的压缩阈值：超长结果截断，完整结果仍进审计。
TOOL_RESULT_MAX_CHARS = 800
WEB_SEARCH_MAX_REFERENCES = 3
TOOL_RESULT_TRUNCATED_SUFFIX = "（内容过长已截断）"


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


def _part_field(part: Any, key: str, default: Any = "") -> Any:
    """读取工具调用分片字段：兼容 dict（langchain 实际形态）与对象两种形态。"""
    if isinstance(part, dict):
        return part.get(key, default)
    return getattr(part, key, default)


def _merge_streamed_tool_calls(chunk: Any, accumulated: list[dict]) -> list[dict]:
    """合并流式工具调用：分片增量始终累积，完整条目只补充不覆盖。

    dashscope 兼容模式的真实流：首个 chunk 带完整 name（args 为空占位），
    随后 args 以 ``tool_call_chunks`` 的 JSON 分片陆续下发，最后再追加
    ``name=''`` 的空名占位条目。因此：
    - ``tool_call_chunks`` 分片始终按 index 累积（args 拼接）；
    - ``tool_calls`` 中 name 非空的条目用于补充 name/id，args 仅在非空时覆盖；
    - name 为空的占位条目一律忽略。
    """
    partials = list(getattr(chunk, "tool_call_chunks", None) or [])
    for part in partials:
        index = int(_part_field(part, "index", 0) or 0)
        while len(accumulated) <= index:
            accumulated.append({"name": "", "args": "", "id": ""})
        target = accumulated[index]
        name = str(_part_field(part, "name") or "").strip()
        if name:
            target["name"] = name
        args = str(_part_field(part, "args") or "")
        if args:
            # args 以 JSON 字符串分片增量下发，按片段累积。
            target["args"] = f"{target['args']}{args}"
        call_id = str(_part_field(part, "id") or "")
        if call_id:
            target["id"] = call_id
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


def _safe_int(value: Any) -> int:
    """容错转 int：缺失 / 非法值一律记 0，绝不抛异常。"""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_chunk_usage(chunk: Any) -> tuple[int, int]:
    """从流式 chunk 提取 (prompt_tokens, completion_tokens)。

    优先 langchain 的 usage_metadata，兼容 input_tokens/output_tokens 与
    prompt_tokens/completion_tokens 两套命名；拿不到就记 0，绝不因缺失报错。
    """
    usage_metadata = getattr(chunk, "usage_metadata", None)
    prompt = completion = 0
    if isinstance(usage_metadata, dict):
        prompt = _safe_int(usage_metadata.get("input_tokens") or usage_metadata.get("prompt_tokens"))
        completion = _safe_int(usage_metadata.get("output_tokens") or usage_metadata.get("completion_tokens"))
    elif usage_metadata is not None:
        prompt = _safe_int(getattr(usage_metadata, "input_tokens", None) or getattr(usage_metadata, "prompt_tokens", None))
        completion = _safe_int(getattr(usage_metadata, "output_tokens", None) or getattr(usage_metadata, "completion_tokens", None))
    if not prompt:
        prompt = _safe_int(getattr(chunk, "input_tokens", None) or getattr(chunk, "prompt_tokens", None))
    if not completion:
        completion = _safe_int(getattr(chunk, "output_tokens", None) or getattr(chunk, "completion_tokens", None))
    return prompt, completion


def _prepare_tool_content(name: str, result: Any) -> str:
    """组装写入 ToolMessage 的文本：web_search 的 references 最多保留前 N 条，
    超长结果截断并追加标记。完整结果仍由调用方写入审计 response_json。"""
    display = result
    if name == "web_search" and isinstance(result, dict) and isinstance(result.get("references"), list):
        references = result.get("references")
        if len(references) > WEB_SEARCH_MAX_REFERENCES:
            display = {**result, "references": references[:WEB_SEARCH_MAX_REFERENCES]}
    content_text = serialize_tool_result(display)
    if len(content_text) > TOOL_RESULT_MAX_CHARS:
        content_text = content_text[:TOOL_RESULT_MAX_CHARS] + TOOL_RESULT_TRUNCATED_SUFFIX
    return content_text


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
    memory_context: dict | None = None,
) -> list[Any]:
    recent_messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == thread.id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(RECENT_MESSAGE_DB_WINDOW)
    ).all()
    ordered = list(reversed(list(recent_messages)))
    # 记忆只检索一次：act_step 首轮检索后经 memory_context 传入，多轮复用，
    # 不再每轮重复向量检索。传入 None（旧调用方）时按空记忆处理，保持兼容。
    memory_context = memory_context or {"summary": "", "items": []}
    memory_text = ContextAssembler.build_memory_context(
        memory_summary=str(memory_context.get("summary") or ""),
        memory_items=list(memory_context.get("items") or []),
    )
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
    # 系统提示词置于最前：知识盲区/时效性信息必须先联网搜索再回答。
    messages.insert(0, SystemMessage(content=GENERAL_CHAT_SYSTEM_PROMPT))
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
    iteration = int(state.get("iteration_count") or 0)
    running_step = {"seq": len(list(state.get("reasoning_steps") or [])) + 1, "step_type": "act", "label": "行动", "content": "", "status": "running", "iteration": iteration}
    emit(build_reasoning_step_event(assistant_message.id, running_step))
    # 记忆只检索一次：首轮（iteration 0）且 state 无 memory_payload 时检索一次，
    # 后续轮次从 state 复用，避免多轮向量检索重复浪费。检索失败按空记忆降级。
    memory_payload = state.get("memory_payload")
    if memory_payload is None and iteration == 0:
        try:
            memory_context = MemoryService().retrieve_context(
                db,
                user_id=thread.user_id,
                query_text=state.get("user_message") or "",
            )
            memory_payload = {"summary": str(memory_context.summary or ""), "items": list(memory_context.items or [])}
        except Exception as exc:
            logger.warning(
                "agent_memory_retrieval_degraded run_id=%s conversation_id=%s reason=%s fallback=empty",
                state.get("agent_run_id"),
                state.get("conversation_id"),
                type(exc).__name__,
            )
            memory_payload = {"summary": "", "items": []}
    if memory_payload is None:
        memory_payload = {"summary": "", "items": []}
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
        memory_context=memory_payload,
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
    prompt_tokens = completion_tokens = 0
    started_at = time.monotonic()
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
            # 流式 chunk 的 usage 只在最后/聚合 chunk 带总量：取最大值而非逐块累加。
            chunk_prompt, chunk_completion = _extract_chunk_usage(chunk)
            if chunk_prompt:
                prompt_tokens = max(prompt_tokens, chunk_prompt)
            if chunk_completion:
                completion_tokens = max(completion_tokens, chunk_completion)
    finally:
        # 完成 / 取消 / 失败统一强制刷新，避免 DB 文本落后于客户端已显示文本。
        text_buffer.flush()
    latency_ms = int(round((time.monotonic() - started_at) * 1000))
    round_tokens = {"prompt_tokens": int(prompt_tokens), "completion_tokens": int(completion_tokens), "latency_ms": latency_ms}
    # 持久化本轮运行指标：token 记账 + 逐轮 step_metrics（JSON 列表追加）。
    # 无 usage 时 total_tokens 保持 None（不伪造 0），step_metrics 仍记录本轮耗时。
    step_metric = {
        "iteration": iteration,
        "step_type": "act",
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "latency_ms": latency_ms,
    }
    existing_metrics = list(getattr(agent_run, "step_metrics", None) or [])
    setattr(agent_run, "step_metrics", existing_metrics + [step_metric])
    if prompt_tokens or completion_tokens:
        current_total = getattr(agent_run, "total_tokens", None) or 0
        setattr(agent_run, "total_tokens", current_total + prompt_tokens + completion_tokens)
    db.commit()
    # args 经序列化归一为 dict（合并缓冲中可能是 JSON 字符串或空串），
    # 避免 AIMessage 的 pydantic 校验失败。
    normalized_tool_calls = serialize_tool_calls(raw_tool_calls)
    aimessage = make_aimessage(iteration_text, normalized_tool_calls)
    completed_step = {**running_step, "content": iteration_text or "（无文本输出）", "status": "completed"}
    emit(build_reasoning_step_event(assistant_message.id, completed_step))
    return {
        "aimessage": serialize_aimessage(aimessage),
        "pending_tool_calls": normalized_tool_calls,
        "iteration": iteration + 1,
        "steps": [completed_step],
        "memory_payload": memory_payload,
        "round_tokens": round_tokens,
    }


async def observe_step(db: Session, thread: ConversationThread, assistant_message: ConversationMessage, agent_run: AgentRun, *, state: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    del thread
    tool_calls = list(state.get("pending_tool_calls") or [])
    running_step = {"seq": len(list(state.get("reasoning_steps") or [])) + 1, "step_type": "observe", "label": "观察", "content": "", "status": "running", "iteration": int(state.get("iteration_count") or 0)}
    emit(build_reasoning_step_event(assistant_message.id, running_step))
    if not tool_calls:
        completed_step = {**running_step, "content": "本轮无工具调用", "status": "completed"}
        emit(build_reasoning_step_event(assistant_message.id, completed_step))
        return {"tool_messages": [], "observation": "本轮无工具调用", "tool_failed": False, "tool_failed_all": False, "web_search_called": False, "pending_tool_calls": [], "steps": [completed_step]}
    raise_if_stream_cancelled(db, state.get("stream_id"), force_database_check=True)
    tool_map = {tool.name: tool for tool in await build_agent_tools(include_names=GENERAL_CHAT_ACTIVE_TOOLS)}
    # 用 index 槽位回填结果，保证 tool_messages/summaries 与原始 tool_calls 顺序一致
    # （并发完成顺序可能与调用顺序不同，错位会破坏模型对工具结果的理解）。
    tool_messages: list[dict[str, Any]] = [{} for _ in tool_calls]
    summaries: list[str] = ["" for _ in tool_calls]
    tool_failed = False
    failures = 0
    web_search_called = False  # 本轮是否尝试调用 web_search（供 run 级 searched_in_run 累计）
    real_jobs: list[dict[str, Any]] = []

    # 第一遍：空名 / 未知工具等廉价失败路径保持同步处理；真实工具收集起来并发执行。
    for idx, call in enumerate(tool_calls):
        name = str(call.get("name") or "").strip()
        args, call_id = call.get("args") or {}, str(call.get("id") or mint_token())
        if not name:
            # 名称为空的残缺调用：直接按失败收口，不进入“未知工具”空转。
            tool_failed = True
            failures += 1
            content_text = "模型尝试调用工具但未给出工具名，已忽略。"
            emit({"event": "tool_call_failed", "data": {"tool_name": "", "call_id": call_id, "message": content_text}})
            tool_messages[idx] = {"role": "tool", "content": content_text, "name": "", "tool_call_id": call_id}
            summaries[idx] = content_text[:80]
            continue
        if name == "web_search":
            web_search_called = True
            if not str(args.get("query") or "").strip():
                # 部分模型会以空参数调用联网搜索：回退用本轮用户消息作为搜索词。
                user_query = str(state.get("user_message") or "").strip()
                if user_query:
                    args = {"query": user_query}
        tool = tool_map.get(name)
        emit({"event": "tool_call_started", "data": {"tool_name": name, "call_id": call_id}})
        audit = start_tool_audit(db, agent_run_id=agent_run.id, tool_name=name, request_json={"arguments": args})
        if tool is None:
            tool_failed = True
            failures += 1
            content_text = f"未知工具：{name}"
            finish_tool_audit(db, audit, status="failed", response_json={}, error_message=content_text)
            emit({"event": "tool_call_failed", "data": {"tool_name": name, "call_id": call_id, "message": content_text}})
            tool_messages[idx] = {"role": "tool", "content": content_text, "name": name, "tool_call_id": call_id}
            summaries[idx] = f"{name}: {content_text[:80]}"
        else:
            real_jobs.append({"idx": idx, "name": name, "args": args, "call_id": call_id, "tool": tool, "audit": audit})

    # 第二遍：真实工具经 asyncio.gather + Semaphore 并发执行；每个工具独立 try/except，
    # 单个失败不影响其他工具。计时与结果收集在纯异步协程内完成，不触碰 DB 会话，
    # 审计/SSE 回填统一在 gather 之后按原始顺序进行。
    if real_jobs:
        semaphore = asyncio.Semaphore(GENERAL_CHAT_TOOL_CONCURRENCY)

        async def _invoke(job: dict[str, Any]) -> dict[str, Any]:
            started = time.monotonic()
            async with semaphore:
                try:
                    result = await job["tool"].ainvoke(job["args"])
                    return {"result": result, "error": None, "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
                except Exception as exc:
                    return {"result": None, "error": exc, "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}

        outcomes = await asyncio.gather(*(_invoke(job) for job in real_jobs))

        for job, outcome in zip(real_jobs, outcomes):
            idx, name, call_id, audit = job["idx"], job["name"], job["call_id"], job["audit"]
            audit.latency_ms = outcome["elapsed_ms"]
            if outcome["error"] is None:
                result = outcome["result"]
                content_text = _prepare_tool_content(name, result)
                finish_tool_audit(db, audit, status="ok", response_json=serialize_any(result))
                emit({"event": "tool_call_completed", "data": {"tool_name": name, "call_id": call_id}})
            else:
                tool_failed = True
                failures += 1
                content_text = f"工具执行失败：{outcome['error']}"
                finish_tool_audit(db, audit, status="failed", response_json={}, error_message=str(outcome["error"]))
                emit({"event": "tool_call_failed", "data": {"tool_name": name, "call_id": call_id, "message": str(outcome["error"])}})
            tool_messages[idx] = {"role": "tool", "content": content_text, "name": name, "tool_call_id": call_id}
            summaries[idx] = f"{name}: {content_text[:80]}"

    observation = "；".join(summaries)[:120] or "本轮无工具调用"
    completed_step = {**running_step, "content": observation, "status": "completed"}
    emit(build_reasoning_step_event(assistant_message.id, completed_step))
    return {"tool_messages": tool_messages, "observation": observation, "tool_failed": tool_failed, "tool_failed_all": failures > 0 and failures >= len(tool_calls), "web_search_called": web_search_called, "pending_tool_calls": [], "steps": [completed_step]}


async def reflect_step(db: Session, thread: ConversationThread, assistant_message: ConversationMessage, agent_run: AgentRun, *, state: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    del thread, agent_run
    settings = get_settings()
    iteration = int(state.get("iteration_count") or 0)
    max_iter = int(state.get("max_iterations") or settings.agent_max_loop_iterations)
    aimessage = state.get("current_aimessage") or {}
    had_tool_calls = bool(aimessage.get("tool_calls"))
    observation = str(state.get("observation") or "")
    # 本轮回答文本只取本轮 act 的输出；不要用历史累积的 text_content 兜底，
    # 否则上一轮承诺话术会让 reflect 误判“已有回答”而跳过工具轮。
    assistant_output = str(aimessage.get("content") or "").strip()
    tool_failed = bool(state.get("tool_failed"))
    tool_failed_all = bool(state.get("tool_failed_all"))
    decision, rationale, follow_up_prompt = "done", "", None
    degraded = False
    anti_repeat_triggered = False
    anti_commitment_triggered = False
    search_forced_triggered = False
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
    elif (
        not state.get("anti_commitment_used")
        and not had_tool_calls
        and assistant_output
        and _is_promise_only_answer(
            str(state.get("user_message") or ""),
            assistant_output,
        )
    ):
        # 承诺话术防呆：搜索/实时信息类请求，模型只给出“我来帮你搜索”等承诺
        # 而没有实际调用工具时，强制再跑一轮并明确要求调用 web_search。
        decision = "continue"
        rationale = "仅给出承诺性答复未执行搜索，要求调用工具"
        follow_up_prompt = "你刚才只给出了承诺性的答复，没有实际调用任何工具。请立即调用 web_search 工具执行搜索，再基于搜索结果给出最终回答。"
        anti_commitment_triggered = True
    elif (
        not state.get("search_forced")
        and not had_tool_calls
        and assistant_output
        and not state.get("searched_in_run")
        and _requires_fresh_information(str(state.get("user_message") or ""))
        and iteration < max_iter
    ):
        # 知识盲区强制搜索护栏：问题涉及时效性/无法确认的信息（新闻、价格、
        # 行情、最新进展等），但本轮直接凭训练知识作答、且整个 run 从未调用过
        # web_search —— 强制再跑一轮要求先搜索（单次，防死循环）。
        decision = "continue"
        rationale = "问题需要联网获取最新信息，强制要求调用搜索"
        follow_up_prompt = "该问题涉及时效性或你的训练知识无法确认的信息。请立即调用 web_search 工具搜索后再回答，禁止仅凭训练知识作答。"
        search_forced_triggered = True
    elif iteration >= max_iter:
        rationale = f"已达最大迭代次数（{max_iter}）"
    elif tool_failed_all:
        rationale = "工具执行失败，避免重复调用"
    elif had_tool_calls:
        # 核心修复：act 流中模型在发起工具调用之前会先流出“预告/承诺”文本
        # （如“我来为您搜索……”），该文本不可能包含工具结果，绝不能当作
        # 最终回答。因此只要本轮调用了工具，就必须再跑一轮 act，让模型基于
        # ToolMessage 生成最终回答；部分失败（仍有工具成功）同样继续，只有
        # 全部失败才收口，避免“搜索成功却直接收口、没有后文”的线上问题。
        decision = "continue"
        rationale = "工具已执行，需基于工具结果生成最终回答"
        follow_up_prompt = "工具已执行完成。请直接基于工具返回的结果，针对用户当前输入整理出简洁的最终回答文本，必须输出非空内容。"
    elif assistant_output:
        rationale = "本轮无工具调用，回答完整"
    else:
        # 防御性兜底：正常流程下此分支不可达（工具轮已在上方确定性续跑），
        # 保留 LLM 评估以兼容未来新增的循环形态与 legacy 调用方。
        try:
            evidence = {"user_goal": str(state.get("user_message") or "")[:1200], "plan": str(state.get("plan") or "")[:600], "iteration": iteration, "max_iterations": max_iter, "current_assistant_output": assistant_output[:2000], "observation": observation[:1600], "tool_failed": tool_failed, "tool_messages": [{"name": str(item.get("name") or "")[:120], "content": str(item.get("content") or "")[:1200]} for item in list(state.get("agent_messages") or []) if item.get("role") == "tool"][-4:]}
            result = await llm.ainvoke_structured(settings, schema=llm.ReflectDecision, system_prompt="你是 Synora 的执行评估器。根据提供的执行证据判断是否需要下一轮行动。只有当「已有面向用户的回答文本」时才可令 is_complete=true；如果工具结果已经充分，但还没有生成面向用户的回答文本，必须令 is_complete=false，并给出生成最终回答的任务指引。follow_up_prompt 只能是给模型的简短任务指引，不得包含密钥、令牌、完整附件、用户隐私原文或工具内部错误详情。", user_text=json.dumps(evidence, ensure_ascii=False), operation="agent_reflect")
            if result.is_complete:
                if not assistant_output:
                    # LLM 判定“信息已充分”但当前还没有面向用户的回答文本：
                    # 强制再跑一轮 act 生成回答，避免“工具成功但空气泡”收尾。
                    decision = "continue"
                    rationale = "信息已充分但尚未生成回答文本，要求基于工具结果作答"
                    follow_up_prompt = "基于已获得的工具结果，直接针对用户当前输入整理出简洁的最终回答文本，必须输出非空内容。"
                else:
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
        "anti_commitment_used": bool(state.get("anti_commitment_used")) or anti_commitment_triggered,
        "search_forced": bool(state.get("search_forced")) or search_forced_triggered,
    }


# 搜索/实时信息类意图提示词（用户输入中命中任一即认为“需要工具”）
_SEARCH_INTENT_HINTS = (
    "搜",
    "查",
    "价格",
    "最新",
    "新闻",
    "多少",
    "情况",
    "行情",
    "天气",
    "汇率",
    "信息",
    "动态",
)
# 承诺性话术提示词（assistant 输出短文本且含这些词，视为“只承诺未执行”）
_COMMITMENT_HINTS = (
    "帮你",
    "我来",
    "好的",
    "让我",
    "稍等",
    "马上",
    "正在",
    "这就",
    "请稍",
    "先帮",
)
# 时效性/无法确认信息提示词（命中即要求联网搜索，配合知识盲区强制搜索护栏）。
# 刻意排除“现在/今天/多少”等易误伤日常表达的宽泛词。
_FRESHNESS_HINTS = (
    "最新",
    "新闻",
    "价格",
    "行情",
    "汇率",
    "天气",
    "目前",
    "进展",
    "更新",
    "排名",
    "榜单",
    "比分",
    "政策",
    "现状",
    "动态",
    "多少钱",
)
_YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")


def _requires_fresh_information(user_message: str) -> bool:
    """判断问题是否依赖时效性/无法确认的信息（命中时效提示词或年份数字）。"""
    if not user_message:
        return False
    return any(hint in user_message for hint in _FRESHNESS_HINTS) or bool(_YEAR_PATTERN.search(user_message))


def _is_promise_only_answer(user_message: str, assistant_output: str) -> bool:
    """检测“承诺性话术”：搜索/实时信息类请求只得到一句短承诺而没有实际动作。

    同时满足：用户输入含搜索意图词、assistant 输出为短文本（<=60 字）、
    文本含承诺话术词。正常的知识性回答（如“LLM 是大语言模型……”）不含
    承诺词，不会被误判。
    """
    if not (user_message and assistant_output):
        return False
    if len(assistant_output) > 60:
        return False
    return any(hint in user_message for hint in _SEARCH_INTENT_HINTS) and any(
        hint in assistant_output for hint in _COMMITMENT_HINTS
    )


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
