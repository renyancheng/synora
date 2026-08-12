from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator, Callable, Iterable
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import llm
from app.agent.tools import build_agent_tools
from app.config import get_settings
from app.domains.attachment.service import build_attachment_prompt_assets
from app.domains.conversation.history_search_service import ConversationHistorySearchService
from app.domains.memory.service import MemoryService
from app.domains.quick_note.service import save_note_after_approval
from app.domains.schedule.service import build_draft_hash, create_schedule_after_approval, detect_conflicts, normalize_reminder_preset
from app.models import AgentRun, AgentToolCallAudit, ApprovalRequest, Attachment, ConversationMessage, ConversationPendingState, ConversationThread
from app.runtime.context_assembler import ContextAssembler
from app.runtime.errors import LLMServiceError
from app.runtime.mcp_client import invoke_synora_tool
from app.schemas.conversation import ConversationActionRequest, ConversationSendMessageRequest
from app.schemas.quick_note import QuickNoteDraftRequest
from app.schemas.schedule import ScheduleEventDraft
from app.security import mint_token, sha256_text
from app.tasks.memory import write_user_memory

logger = logging.getLogger(__name__)

NON_EDITABLE_CARD_MESSAGE_TYPES = {
    "schedule_draft_card",
    "quick_note_preview_card",
    "conflict_card",
}
RECENT_MESSAGE_DB_WINDOW = 12
RECENT_MESSAGE_LLM_WINDOW = 8
CONVERSATION_HISTORY_SCAN_LIMIT = 160
CONVERSATION_HISTORY_PICK_LIMIT = 6
CONVERSATION_HISTORY_SNIPPET_LENGTH = 140
CONVERSATION_REFERENCE_HINTS = (
    "之前",
    "前面",
    "刚才",
    "上次",
    "前文",
    "继续",
    "总结",
    "回顾",
    "那个",
)

# general_chat 分支不注入的 intake 写工具：避免 LLM 自主调用这些工具产出
# 无卡片、无 pending 的“伪草稿”，保证日程/速记创建统一走 intake 节点。
GENERAL_CHAT_EXCLUDED_TOOLS = {
    "parse_schedule_draft",
    "detect_schedule_conflicts",
    "create_schedule_after_approval",
    "prepare_quick_note_draft",
    "create_quick_note_after_approval",
}


def _schedule_checkpoint_cleanup(thread_ids: Iterable[str | None]) -> None:
    """尽力而为地删除孤儿 checkpoint（rewind / delete_conversation 后）。

    AsyncSqliteSaver 删除是异步的，这里在已运行的事件循环里调度 task，
    否则新建临时循环执行；失败仅记日志，不阻断主流程。
    """
    ids = [tid for tid in thread_ids if tid]
    if not ids:
        return

    from app.agent.checkpointer import delete_checkpoint

    async def _run() -> None:
        for thread_id in ids:
            await delete_checkpoint(thread_id)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        asyncio.create_task(_run())
    else:
        try:
            asyncio.run(_run())
        except Exception:
            logger.exception("checkpoint_cleanup_failed")


def _enqueue_memory_writeback(*, user_id: int, source_kind: str, source_ref_id: str | None, text: str, summary: str = '') -> None:
    try:
        write_user_memory.delay(
            user_id=user_id,
            source_kind=source_kind,
            source_ref_id=source_ref_id,
            text=text,
            summary=summary,
        )
    except Exception:
        return


DEFAULT_THREAD_TITLE = "新对话"


def _normalize_source_history(*values: Any) -> list[str]:
    history: list[str] = []
    for value in values:
        if isinstance(value, str):
            candidate = value.strip()
            if candidate and candidate not in history:
                history.append(candidate)
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    candidate = item.strip()
                    if candidate and candidate not in history:
                        history.append(candidate)
    return history


def _schedule_scope_from_action_group(action_group_id: str | None) -> str | None:
    if not action_group_id:
        return None
    return f"conversation_schedule:{action_group_id}"


def _quick_note_scope_from_action_group(action_group_id: str | None) -> str | None:
    if not action_group_id:
        return None
    return f"conversation_quick_note:{action_group_id}"


def _schedule_draft_summary(draft: ScheduleEventDraft) -> str:
    parts = [draft.title.strip()]
    if draft.location:
        parts.append(f"地点：{draft.location.strip()}")
    if draft.details.strip():
        parts.append(f"详情：{draft.details.strip()}")
    parts.append(f"开始：{draft.start.date_time.isoformat()}")
    parts.append(f"结束：{draft.end.date_time.isoformat()}")
    return "\n".join(part for part in parts if part)


def _tool_context(
    context: dict[str, Any],
    *,
    user_id: int,
    approval_scope: str | None = None,
) -> dict[str, Any]:
    tool_context: dict[str, Any] = {}
    for key, value in context.items():
        if value is None:
            continue
        if isinstance(value, list):
            tool_context[str(key)] = [str(item) for item in value if item is not None]
            continue
        if isinstance(value, dict):
            tool_context[str(key)] = {str(child_key): value[child_key] for child_key in value}
            continue
        tool_context[str(key)] = str(value)
    tool_context["user_id"] = str(user_id)
    if approval_scope:
        tool_context["approval_scope"] = approval_scope
    return tool_context


def _extract_history_query_terms(text: str) -> list[str]:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return []
    terms: list[str] = [normalized]
    for item in re.findall(r"[a-z0-9_]{2,}", normalized):
        if item not in terms:
            terms.append(item)
    stop_chars = {"我", "你", "他", "她", "它", "的", "了", "吗", "呢", "啊", "呀", "吧", "是", "在", "把", "要", "和"}
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        if chunk not in terms:
            terms.append(chunk)
        meaningful_chars = [char for char in chunk if char not in stop_chars]
        for size in range(2, min(5, len(meaningful_chars) + 1)):
            for index in range(0, len(meaningful_chars) - size + 1):
                token = "".join(meaningful_chars[index : index + size])
                if token and token not in terms:
                    terms.append(token)
    return terms


def _history_char_overlap_score(query_text: str, candidate_text: str) -> float:
    ignored = {"我", "你", "他", "她", "它", "的", "了", "吗", "呢", "啊", "呀", "吧", "是", "在", "和", "就", "也"}
    query_chars = {char for char in query_text if "\u4e00" <= char <= "\u9fff" and char not in ignored}
    candidate_chars = {char for char in candidate_text if "\u4e00" <= char <= "\u9fff" and char not in ignored}
    if not query_chars or not candidate_chars:
        return 0.0
    return float(len(query_chars & candidate_chars))


def _score_history_message(query_text: str, terms: list[str], message: ConversationMessage) -> float:
    content = str(message.text_content or "").strip().lower()
    if not content:
        return 0.0
    score = _history_char_overlap_score(query_text.lower(), content)
    for term in terms:
        cleaned = term.strip().lower()
        if len(cleaned) < 2:
            continue
        if cleaned in content:
            score += max(1, content.count(cleaned)) * (2.5 if len(cleaned) >= 4 else 1.2)
    if message.role == "user":
        score += 0.5
    return score


def _truncate_history_text(text: str, limit: int = CONVERSATION_HISTORY_SNIPPET_LENGTH) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _build_conversation_history_recall(
    db: Session,
    *,
    conversation_id: int,
    current_user_message_id: int | None,
) -> list[str]:
    current_message = db.get(ConversationMessage, current_user_message_id) if current_user_message_id else None
    query_text = str(getattr(current_message, "text_content", "") or "").strip()
    if not query_text:
        return []

    raw_rows = db.scalars(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.message_type == "text",
        )
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(RECENT_MESSAGE_DB_WINDOW + CONVERSATION_HISTORY_SCAN_LIMIT + 24)
    ).all()
    filtered_rows = [
        row
        for row in raw_rows
        if row.id != current_user_message_id and str(row.text_content or "").strip()
    ]
    older_rows = filtered_rows[RECENT_MESSAGE_DB_WINDOW:]
    if not older_rows:
        return []

    terms = _extract_history_query_terms(query_text)
    ranked: list[tuple[float, ConversationMessage]] = []
    for row in older_rows:
        score = _score_history_message(query_text, terms, row)
        if score > 0:
            ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], item[1].created_at, item[1].id))

    selected_rows: list[ConversationMessage] = []
    seen_ids: set[int] = set()
    for _, row in ranked[:CONVERSATION_HISTORY_PICK_LIMIT]:
        if row.id not in seen_ids:
            selected_rows.append(row)
            seen_ids.add(row.id)

    if not selected_rows and any(hint in query_text for hint in CONVERSATION_REFERENCE_HINTS):
        for row in reversed(older_rows[:CONVERSATION_HISTORY_PICK_LIMIT]):
            if row.id not in seen_ids:
                selected_rows.append(row)
                seen_ids.add(row.id)

    selected_rows.sort(key=lambda item: (item.created_at, item.id))
    return [
        f"{'用户' if row.role == 'user' else '助手'}：{_truncate_history_text(str(row.text_content or ''))}"
        for row in selected_rows
    ]


def _resolve_conversation_history_lines(
    db: Session,
    *,
    conversation_id: int,
    current_user_message_id: int | None,
) -> list[str]:
    semantic_lines = ConversationHistorySearchService().retrieve_history_lines(
        db,
        conversation_id=conversation_id,
        current_user_message_id=current_user_message_id,
        recent_window=RECENT_MESSAGE_DB_WINDOW,
    )
    if semantic_lines:
        return semantic_lines
    return _build_conversation_history_recall(
        db,
        conversation_id=conversation_id,
        current_user_message_id=current_user_message_id,
    )



def _error_payload(exc: Exception, *, assistant_message_id: int) -> dict[str, object]:
    # LangGraph 会把节点异常包装进 Pregel/Runnable 链，需沿 __cause__/__context__
    # 解包到最内层的业务异常，避免丢失 LLMServiceError 的 code。
    unwrapped = exc
    seen: set[int] = set()
    while not isinstance(unwrapped, LLMServiceError):
        cause = getattr(unwrapped, "__cause__", None) or getattr(unwrapped, "__context__", None)
        if cause is None or id(cause) in seen:
            break
        seen.add(id(cause))
        unwrapped = cause
    if isinstance(unwrapped, LLMServiceError):
        return {
            "assistant_message_id": assistant_message_id,
            "code": unwrapped.code,
            "message": unwrapped.message,
            "retryable": unwrapped.retryable,
        }
    return {
        "assistant_message_id": assistant_message_id,
        "code": "conversation_stream_error",
        "message": str(exc),
        "retryable": False,
    }


def list_conversations(db: Session, user_id: int) -> list[ConversationThread]:
    return db.scalars(
        select(ConversationThread)
        .where(ConversationThread.user_id == user_id)
        .order_by(ConversationThread.created_at.desc(), ConversationThread.id.desc())
    ).all()


def create_conversation(db: Session, user_id: int, *, title: str | None = None) -> ConversationThread:
    thread = ConversationThread(
        user_id=user_id,
        title=(title or DEFAULT_THREAD_TITLE).strip() or DEFAULT_THREAD_TITLE,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


def get_conversation(db: Session, user_id: int, conversation_id: int) -> ConversationThread:
    thread = db.scalar(
        select(ConversationThread).where(
            ConversationThread.id == conversation_id,
            ConversationThread.user_id == user_id,
        )
    )
    if not thread:
        raise ValueError("对话不存在或无权访问。")
    return thread


def list_messages(db: Session, user_id: int, conversation_id: int) -> tuple[ConversationThread, list[ConversationMessage]]:
    thread = get_conversation(db, user_id, conversation_id)
    messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
    ).all()
    return thread, list(messages)


def update_conversation_title(db: Session, user_id: int, conversation_id: int, title: str) -> ConversationThread:
    thread = get_conversation(db, user_id, conversation_id)
    normalized = title.strip()
    if not normalized:
        raise ValueError("对话标题不能为空。")
    thread.title = normalized[:120]
    db.commit()
    db.refresh(thread)
    return thread


def delete_conversation(db: Session, user_id: int, conversation_id: int) -> None:
    thread = get_conversation(db, user_id, conversation_id)
    pending = _get_pending_state(db, conversation_id)
    messages = db.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id == conversation_id)).all()
    text_message_ids = [message.id for message in messages if message.message_type == "text"]
    action_group_ids = {
        str(message.action_group_id)
        for message in messages
        if message.action_group_id
    }
    if pending is not None:
        pending_action_group_id = str(pending.meta_json.get("action_group_id") or "").strip()
        if pending_action_group_id:
            action_group_ids.add(pending_action_group_id)
    _delete_approvals_for_action_groups(
        db,
        user_id=user_id,
        action_group_ids=action_group_ids,
        approval_token=pending.approval_token if pending is not None else None,
    )
    agent_runs = db.scalars(select(AgentRun).where(AgentRun.conversation_id == conversation_id)).all()
    checkpoint_thread_ids: list[str] = []
    if agent_runs:
        # tool_call_audits 由 AgentRun.tool_call_audits cascade="all, delete-orphan" 级联删除
        for run in agent_runs:
            if run.checkpoint_thread_id:
                checkpoint_thread_ids.append(run.checkpoint_thread_id)
            db.delete(run)
    db.delete(thread)
    db.commit()
    _schedule_checkpoint_cleanup(checkpoint_thread_ids)
    ConversationHistorySearchService().delete_messages(
        conversation_id=conversation_id,
        message_ids=text_message_ids,
    )


def rewind_last_turn(db: Session, user_id: int, conversation_id: int) -> tuple[ConversationThread, ConversationMessage]:
    thread = get_conversation(db, user_id, conversation_id)
    messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
    ).all()
    user_message = db.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "user",
        )
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(1)
    )
    if user_message is None:
        raise ValueError("当前没有可撤回的消息。")
    message_index = next((index for index, item in enumerate(messages) if item.id == user_message.id), -1)
    if message_index < 0:
        raise ValueError("当前没有可撤回的消息。")
    if any(item.message_type in NON_EDITABLE_CARD_MESSAGE_TYPES for item in messages[message_index + 1:]):
        raise ValueError("当前消息下方已有卡片，不能编辑重发。")

    restored_message = ConversationMessage(
        id=user_message.id,
        conversation_id=user_message.conversation_id,
        role=user_message.role,
        message_type=user_message.message_type,
        status=user_message.status,
        text_content=user_message.text_content,
        structured_payload_json=dict(user_message.structured_payload_json or {}),
        action_group_id=user_message.action_group_id,
        revision=user_message.revision,
        created_at=user_message.created_at,
    )

    agent_run = db.scalar(
        select(AgentRun)
        .where(
            AgentRun.conversation_id == conversation_id,
            AgentRun.user_message_id == user_message.id,
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    )
    created_message_ids: list[int] = []
    assistant_message_id: int | None = None
    checkpoint_thread_ids: list[str] = []
    deleted_text_message_ids: set[int] = {user_message.id} if user_message.message_type == "text" else set()
    if agent_run is not None:
        created_message_ids = [int(item) for item in list(agent_run.output_json.get("created_message_ids") or []) if isinstance(item, int)]
        assistant_message_id = agent_run.assistant_message_id
        # tool_call_audits 由 AgentRun.tool_call_audits cascade="all, delete-orphan" 级联删除
        if agent_run.checkpoint_thread_id:
            checkpoint_thread_ids.append(agent_run.checkpoint_thread_id)
        db.delete(agent_run)

    for message_id in created_message_ids:
        message = db.get(ConversationMessage, message_id)
        if message is not None:
            if message.message_type == "text":
                deleted_text_message_ids.add(message.id)
            db.delete(message)

    if assistant_message_id is not None:
        assistant_message = db.get(ConversationMessage, assistant_message_id)
        if assistant_message is not None:
            if assistant_message.message_type == "text":
                deleted_text_message_ids.add(assistant_message.id)
            db.delete(assistant_message)

    pending = _get_pending_state(db, conversation_id)
    if pending is not None:
        _delete_approvals_for_action_groups(
            db,
            user_id=user_id,
            action_group_ids={
                str(value)
                for value in (
                    user_message.action_group_id,
                    pending.meta_json.get("action_group_id"),
                )
                if str(value or "").strip()
            },
            approval_token=pending.approval_token,
        )
        db.delete(pending)

    memory_service = MemoryService()

    db.delete(user_message)
    db.commit()
    _schedule_checkpoint_cleanup(checkpoint_thread_ids)

    memory_service.delete_records_by_source(
        db,
        user_id=user_id,
        source_kind="conversation_message",
        source_ref_id=str(user_message.id),
    )
    ConversationHistorySearchService().delete_messages(
        conversation_id=conversation_id,
        message_ids=deleted_text_message_ids,
    )

    latest_message = db.scalar(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(1)
    )
    thread.last_message_at = latest_message.created_at if latest_message is not None else thread.created_at
    db.commit()
    db.refresh(thread)
    return thread, restored_message


def queue_message(
    db: Session,
    user_id: int,
    conversation_id: int,
    payload: ConversationSendMessageRequest,
) -> tuple[ConversationThread, ConversationMessage, ConversationMessage, AgentRun]:
    thread = get_conversation(db, user_id, conversation_id)
    has_user_message = db.scalar(
        select(ConversationMessage.id)
        .where(ConversationMessage.conversation_id == conversation_id, ConversationMessage.role == "user")
        .limit(1)
    )
    text_content = (payload.text_content or "").strip()
    user_message = _append_message(
        db,
        thread,
        role="user",
        message_type="text",
        status="sent",
        text_content=text_content,
        structured_payload=_build_user_message_payload(
            db,
            user_id=user_id,
            attachment_ids=payload.attachment_ids,
            selected_tool=payload.selected_tool,
        ),
    )
    if text_content:
        ConversationHistorySearchService().upsert_message(user_message)
    if has_user_message is None and text_content:
        thread.title = llm.generate_conversation_title(get_settings(), text_content)
        db.commit()
        db.refresh(thread)

    assistant_message = _append_message(
        db,
        thread,
        role="assistant",
        message_type="text",
        status="streaming",
        text_content="",
        structured_payload={},
    )
    agent_run = AgentRun(
        user_id=user_id,
        workflow="conversation_stream",
        status="running",
        conversation_id=thread.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        stream_token=mint_token(),
        stream_status="pending",
        input_json=payload.model_dump(mode="json"),
        output_json={
            "agent_backend": "langchain",
            "tool_source": "mcp",
        },
    )
    db.add(agent_run)
    db.commit()
    db.refresh(agent_run)
    return thread, user_message, assistant_message, agent_run


async def consume_stream(
    db: Session,
    user_id: int,
    conversation_id: int,
    stream_id: str,
) -> AsyncGenerator[dict, None]:
    thread = get_conversation(db, user_id, conversation_id)
    agent_run = db.scalar(
        select(AgentRun).where(
            AgentRun.conversation_id == thread.id,
            AgentRun.stream_token == stream_id,
            AgentRun.user_id == user_id,
        )
    )
    if not agent_run:
        raise ValueError("会话流不存在。")

    assistant_message = db.get(ConversationMessage, agent_run.assistant_message_id)
    if not assistant_message:
        raise ValueError("会话流消息不存在。")

    if agent_run.stream_status == "active":
        raise ValueError("这条消息正在生成中，请稍后再试。")
    if agent_run.stream_status == "completed":
        async for item in _replay_completed_run(db, agent_run, assistant_message):
            yield item
        return

    agent_run.stream_status = "active"
    db.commit()

    payload = dict(agent_run.input_json or {})
    text_content = str(payload.get("text_content") or "")
    attachment_ids = list(payload.get("attachment_ids") or [])
    selected_tool = payload.get("selected_tool")
    context = dict(payload.get("context") or {})
    if agent_run.user_message_id:
        history_lines = _resolve_conversation_history_lines(
            db,
            conversation_id=conversation_id,
            current_user_message_id=agent_run.user_message_id,
        )
        if history_lines:
            context["conversation_history_lines"] = history_lines
    assets = build_attachment_prompt_assets(db, user_id=user_id, attachment_ids=attachment_ids)
    attachment_parts = [part for asset in assets for part in asset.parts]

    yield {
        "event": "run_started",
        "data": {
            "assistant_message_id": assistant_message.id,
            "stream_id": stream_id,
        },
    }

    try:
        if get_settings().agent_backend == "legacy":
            async for item in _consume_stream_legacy(
                db,
                user_id=user_id,
                conversation_id=conversation_id,
                stream_id=stream_id,
                agent_run=agent_run,
                assistant_message=assistant_message,
                thread=thread,
                text_content=text_content,
                attachment_ids=attachment_ids,
                selected_tool=selected_tool,
                attachment_parts=attachment_parts,
                context=context,
            ):
                yield item
            return
        async for item in _consume_stream_graph(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            stream_id=stream_id,
            agent_run=agent_run,
            assistant_message=assistant_message,
            thread=thread,
            text_content=text_content,
            attachment_ids=attachment_ids,
            selected_tool=selected_tool,
            attachment_parts=attachment_parts,
            context=context,
        ):
            yield item
    except Exception as exc:
        agent_run.status = "failed"
        agent_run.stream_status = "failed"
        agent_run.error_message = exc.message if isinstance(exc, LLMServiceError) else str(exc)
        agent_run.completed_at = datetime.now(timezone.utc)
        assistant_message.status = "failed"
        db.commit()
        yield {"event": "run_failed", "data": _error_payload(exc, assistant_message_id=assistant_message.id)}


async def _consume_stream_legacy(
    db: Session,
    *,
    user_id: int,
    conversation_id: int,
    stream_id: str,
    agent_run: AgentRun,
    assistant_message: ConversationMessage,
    thread: ConversationThread,
    text_content: str,
    attachment_ids: list[int],
    selected_tool: Any,
    attachment_parts: list[dict],
    context: dict[str, Any],
) -> AsyncGenerator[dict, None]:
    """旧编排路径（agent_backend=legacy 时启用，用于观察期回滚）。"""
    settings = get_settings()
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
            settings,
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
        "model_name": settings.llm_model,
        "provider_name": "dashscope",
    }
    db.commit()

    if intent == "general_chat":
        async for item in _stream_general_chat(
            db,
            thread,
            assistant_message,
            agent_run,
            user_message=text_content,
            attachment_parts=attachment_parts,
            conversation_history_lines=list(context.get("conversation_history_lines") or []),
        ):
            yield item
        final_text = assistant_message.text_content or ""
        _finalize_run(db, agent_run, assistant_message, assistant_text=final_text, created_message_ids=[])
        yield {"event": "message_completed", "data": {"message": _message_payload(assistant_message)}}
        yield {"event": "run_completed", "data": {"stream_id": stream_id}}
        return

    if intent == "schedule_intake":
        final_text, created_ids, requires_approval, tool_events = await _process_schedule_intake(
            db,
            user_id,
            thread,
            agent_run,
            text_content=text_content,
            attachment_ids=attachment_ids,
            context=context,
            action_group_id=context.get("pending_action_group_id") or None,
            revision=int(context.get("pending_revision") or 1),
        )
    else:
        final_text, created_ids, requires_approval, tool_events = await _process_quick_note_intake(
            db,
            user_id,
            thread,
            agent_run,
            text_content=text_content,
            attachment_ids=attachment_ids,
            context=context,
            action_group_id=context.get("pending_action_group_id") or None,
            revision=int(context.get("pending_revision") or 1),
        )

    for tool_event in tool_events:
        yield tool_event
    async for item in _emit_text_stream(db, assistant_message, final_text):
        yield item
    _finalize_run(db, agent_run, assistant_message, assistant_text=final_text, created_message_ids=created_ids)
    yield {"event": "message_completed", "data": {"message": _message_payload(assistant_message)}}
    for message_id in created_ids:
        message = db.get(ConversationMessage, message_id)
        if message:
            yield {"event": "card_snapshot", "data": {"message": _message_payload(message)}}
    if requires_approval:
        yield {"event": "approval_required", "data": requires_approval}
    yield {"event": "run_completed", "data": {"stream_id": stream_id}}


async def _consume_stream_graph(
    db: Session,
    *,
    user_id: int,
    conversation_id: int,
    stream_id: str,
    agent_run: AgentRun,
    assistant_message: ConversationMessage,
    thread: ConversationThread,
    text_content: str,
    attachment_ids: list[int],
    selected_tool: Any,
    attachment_parts: list[dict],
    context: dict[str, Any],
) -> AsyncGenerator[dict, None]:
    """LangGraph 编排路径：图内节点通过 get_stream_writer 实时发事件。

    节点负责 tool_call_* / message_delta / 文本分块，本函数只做图后 DB 收尾，
    保证 message_completed -> card_snapshot -> approval_required -> run_completed
    的 SSE 顺序与旧路径一致。
    """
    from app.agent.checkpointer import setup_checkpointer
    from app.agent.graph import build_graph
    from app.agent.state import AgentState

    await setup_checkpointer()
    graph = build_graph()
    initial: AgentState = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "agent_run_id": agent_run.id,
        "assistant_message_id": assistant_message.id,
        "stream_id": stream_id,
        "user_message": text_content,
        "attachment_ids": attachment_ids,
        "attachment_parts": attachment_parts,
        "context": context,
        "selected_tool": selected_tool,
        "conversation_history_lines": list(context.get("conversation_history_lines") or []),
    }
    thread_id = f"conv_{conversation_id}_run_{agent_run.id}"
    agent_run.checkpoint_thread_id = thread_id
    db.commit()
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db": db,
            "agent_run": agent_run,
            "assistant_message": assistant_message,
            "thread": thread,
        }
    }
    final_update: dict[str, Any] = {}
    async for mode, chunk in graph.astream(initial, config, stream_mode=["updates", "custom"]):
        if mode == "custom":
            yield chunk
        elif isinstance(chunk, dict):
            final_update = chunk.get("finalize") or {}

    final_text = str(final_update.get("assistant_text") or "")
    created_ids = list(final_update.get("created_message_ids") or [])
    requires_approval = final_update.get("requires_approval")
    _finalize_run(db, agent_run, assistant_message, assistant_text=final_text, created_message_ids=created_ids)
    # 推理轨迹持久化：reasoning_step 消息卡片（历史可回溯），经 card_snapshot 推送
    reasoning_steps = list(final_update.get("reasoning_steps") or [])
    if reasoning_steps:
        step_summary = _summarize_reasoning_steps(reasoning_steps)
        step_message = _append_message(
            db,
            thread,
            role="assistant",
            message_type="reasoning_step",
            status="completed",
            text_content=step_summary,
            structured_payload={"steps": reasoning_steps, "summary": step_summary},
        )
        created_ids.append(step_message.id)
    yield {"event": "message_completed", "data": {"message": _message_payload(assistant_message)}}
    for message_id in created_ids:
        message = db.get(ConversationMessage, message_id)
        if message:
            yield {"event": "card_snapshot", "data": {"message": _message_payload(message)}}
    if requires_approval:
        yield {"event": "approval_required", "data": requires_approval}
    yield {"event": "run_completed", "data": {"stream_id": stream_id}}


def apply_action(
    db: Session,
    user_id: int,
    conversation_id: int,
    payload: ConversationActionRequest,
) -> tuple[ConversationThread, list[ConversationMessage]]:
    thread = get_conversation(db, user_id, conversation_id)
    pending = _get_pending_state(db, conversation_id)
    if not pending:
        raise ValueError("当前没有待处理的卡片操作。")

    action = payload.action
    if action == "dismiss_pending_action":
        _mark_action_group_status(
            db,
            pending.meta_json.get("action_group_id"),
            lifecycle_status="cancelled",
            is_actionable=False,
            terminal_summary="已取消本次待确认操作。",
        )
        _clear_pending_state(db, pending)
        return thread, []

    if action == "submit_missing_fields":
        if pending.pending_type != "schedule":
            raise ValueError("当前卡片不支持补充字段。")
        return thread, _submit_schedule_missing_fields(db, user_id, thread, pending, payload.payload)

    if action == "confirm_schedule_draft":
        if pending.pending_type != "schedule" or pending.stage != "approval_pending":
            raise ValueError("当前没有可确认的日程草稿。")
        reminder_preset = payload.payload.get("reminder_preset")
        if reminder_preset:
            draft_payload = dict(pending.payload_json or {})
            draft_payload["reminder_preset"] = normalize_reminder_preset(reminder_preset)
            pending.payload_json = draft_payload
            db.commit()
        return thread, _confirm_schedule_pending(db, user_id, thread, pending)

    if action == "confirm_quick_note":
        if pending.pending_type != "quick_note" or pending.stage != "approval_pending":
            raise ValueError("当前没有可确认的速记草稿。")
        return thread, _confirm_quick_note_pending(db, user_id, thread, pending)

    raise ValueError("不支持的对话动作。")


async def _stream_general_chat(
    db: Session,
    thread: ConversationThread,
    assistant_message: ConversationMessage,
    agent_run: AgentRun,
    *,
    user_message: str,
    attachment_parts: list[dict],
    conversation_history_lines: list[str] | None = None,
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
    async for event in agent.astream_events({"messages": messages}, version="v2"):
        event_name = str(event.get("event") or "")
        if event_name == "on_chat_model_stream":
            delta = _extract_langchain_delta(event)
            if not delta:
                continue
            final_text += delta
            assistant_message.text_content = final_text
            db.commit()
            yield {"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": delta}}
            continue

        if event_name == "on_tool_start":
            tool_name = str(event.get("name") or "tool")
            call_id = str(event.get("run_id") or mint_token())
            tool_audits[call_id] = _start_tool_audit(
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
            _finish_tool_audit(
                db,
                audit,
                status="ok",
                response_json={"output": _serialize_any(event.get("data", {}).get("output"))},
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
            _finish_tool_audit(
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
            tail = _extract_langchain_final_text(event)
            if tail:
                final_text = tail
                assistant_message.text_content = final_text
                db.commit()

    assistant_message.text_content = final_text
    db.commit()


# ---------------------------------------------------------------------------
# plan → act → observe → reflect 主循环（general_chat 专用）
# 节点通过 get_stream_writer() 传入 emit；步骤以持久化 dict 返回，节点写入
# state.reasoning_steps（Annotated add 累积），SSE 事件由 emit 实时写出。
# ---------------------------------------------------------------------------


def _build_reasoning_step_event(assistant_message_id: int, step: dict[str, Any]) -> dict[str, Any]:
    """把持久化 step dict 包装成 SSE reasoning_step 事件。"""
    return {"event": "reasoning_step", "data": {"assistant_message_id": assistant_message_id, **step}}


def _summarize_reasoning_steps(steps: list[dict[str, Any]]) -> str:
    """把步骤序列压成一行摘要（reasoning_step 卡片的 text_content）。"""
    parts: list[str] = []
    for step in steps:
        label = step.get("label") or step.get("step_type") or ""
        content = str(step.get("content") or "").strip()
        if not content or content in ("（无文本输出）", "本轮无工具调用"):
            continue
        parts.append(f"{label}: {content}" if label else content)
    summary = " → ".join(parts)
    return summary[:160] or "推理过程"


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


def _make_aimessage(content: str, tool_calls: list[dict] | None) -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=[
            {"name": tc.get("name", ""), "args": tc.get("args", {}), "id": tc.get("id", ""), "type": "tool_call"}
            for tc in tool_calls or []
        ],
    )


def _serialize_tool_calls(tool_calls: list[dict] | None) -> list[dict[str, Any]]:
    return [
        {"name": tc.get("name", ""), "args": tc.get("args", {}), "id": tc.get("id", "")}
        for tc in tool_calls or []
    ]


def _serialize_aimessage(message: Any) -> dict[str, Any]:
    return {
        "role": "ai",
        "content": llm.extract_message_text(message),
        "tool_calls": _serialize_tool_calls(getattr(message, "tool_calls", None) or []),
    }


def _deserialize_message(item: dict[str, Any]) -> Any:
    role = str(item.get("role") or "")
    content = str(item.get("content") or "")
    if role == "ai":
        return _make_aimessage(content, list(item.get("tool_calls") or []))
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=str(item.get("tool_call_id") or ""),
            name=str(item.get("name") or ""),
        )
    return HumanMessage(content=content)


def _serialize_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        return str(result)


def _build_general_chat_messages(
    db: Session,
    thread: ConversationThread,
    assistant_message: ConversationMessage,
    *,
    user_message: str,
    attachment_parts: list[dict],
    conversation_history_lines: list[str] | None = None,
    agent_messages: list[dict] | None = None,
    follow_up_prompt: str | None = None,
) -> list[Any]:
    """构建 general_chat 的完整消息序列：历史 + 记忆/上下文 + 当前输入 + 跨迭代 agent 消息。"""
    recent_messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == thread.id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(RECENT_MESSAGE_DB_WINDOW)
    ).all()
    ordered = list(reversed(list(recent_messages)))
    memory_context = MemoryService().retrieve_context(db, user_id=thread.user_id, query_text=user_message)
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
    for item in agent_messages or []:
        messages.append(_deserialize_message(item))
    if follow_up_prompt:
        messages.append(HumanMessage(content=str(follow_up_prompt)))
    return messages


async def _plan_step(
    db: Session,
    thread: ConversationThread,
    assistant_message: ConversationMessage,
    agent_run: AgentRun,
    *,
    state: dict[str, Any],
    emit: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    settings = get_settings()
    user_message = state.get("user_message") or ""
    existing = list(state.get("reasoning_steps") or [])
    seq = len(existing) + 1
    try:
        result = await llm.ainvoke_structured(
            settings,
            schema=llm.PlanResult,
            system_prompt=(
                "你是 Synora 的规划助手。用一句话（不超过20字）描述当前回合你要"
                "执行的核心动作，面向用户可读。若只是简单问答，直接给出回答意图。"
            ),
            user_text=user_message,
            operation="agent_plan",
        )
        plan = str(result.plan or "").strip() or "回答用户"
    except Exception:
        plan = "回答用户"
    step = {
        "seq": seq,
        "step_type": "plan",
        "label": "规划",
        "content": plan,
        "status": "completed",
        "iteration": 0,
    }
    emit(_build_reasoning_step_event(assistant_message.id, step))
    return {"plan": plan, "steps": [step]}


async def _act_step(
    db: Session,
    thread: ConversationThread,
    assistant_message: ConversationMessage,
    agent_run: AgentRun,
    *,
    state: dict[str, Any],
    emit: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    settings = get_settings()
    iteration = int(state.get("iteration_count") or 0)
    existing = list(state.get("reasoning_steps") or [])
    seq = len(existing) + 1
    running_step = {
        "seq": seq,
        "step_type": "act",
        "label": "行动",
        "content": "",
        "status": "running",
        "iteration": iteration,
    }
    emit(_build_reasoning_step_event(assistant_message.id, running_step))

    messages = _build_general_chat_messages(
        db,
        thread,
        assistant_message,
        user_message=state.get("user_message") or "",
        attachment_parts=list(state.get("attachment_parts") or []),
        conversation_history_lines=list(state.get("conversation_history_lines") or []),
        agent_messages=list(state.get("agent_messages") or []),
        follow_up_prompt=state.get("follow_up_prompt"),
    )
    model = llm.create_chat_model(settings, temperature=0.35, streaming=True, enable_thinking=False)
    tools = await build_agent_tools(exclude_names=GENERAL_CHAT_EXCLUDED_TOOLS)
    bound_model = model.bind_tools(tools) if tools else model

    final_text = assistant_message.text_content or ""
    iteration_text = ""
    raw_tool_calls: list[dict] = []
    async for chunk in bound_model.astream(messages):
        delta = _extract_model_chunk_delta(chunk)
        if delta:
            iteration_text += delta
            final_text += delta
            assistant_message.text_content = final_text
            db.commit()
            emit({"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": delta}})
        calls = getattr(chunk, "tool_calls", None)
        if calls:
            raw_tool_calls = calls

    aimessage = _make_aimessage(iteration_text, raw_tool_calls)
    completed_step = {
        **running_step,
        "content": iteration_text or "（无文本输出）",
        "status": "completed",
    }
    emit(_build_reasoning_step_event(assistant_message.id, completed_step))
    return {
        "aimessage": _serialize_aimessage(aimessage),
        "pending_tool_calls": _serialize_tool_calls(raw_tool_calls),
        "iteration": iteration + 1,
        "steps": [completed_step],
    }


async def _observe_step(
    db: Session,
    thread: ConversationThread,
    assistant_message: ConversationMessage,
    agent_run: AgentRun,
    *,
    state: dict[str, Any],
    emit: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    tool_calls = list(state.get("pending_tool_calls") or [])
    existing = list(state.get("reasoning_steps") or [])
    seq = len(existing) + 1
    iteration = int(state.get("iteration_count") or 0)
    running_step = {
        "seq": seq,
        "step_type": "observe",
        "label": "观察",
        "content": "",
        "status": "running",
        "iteration": iteration,
    }
    emit(_build_reasoning_step_event(assistant_message.id, running_step))

    tool_messages: list[dict[str, Any]] = []
    summaries: list[str] = []
    if tool_calls:
        tools = await build_agent_tools(exclude_names=GENERAL_CHAT_EXCLUDED_TOOLS)
        tool_map = {tool.name: tool for tool in tools}
        for call in tool_calls:
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            call_id = str(call.get("id") or mint_token())
            tool = tool_map.get(name)
            emit({"event": "tool_call_started", "data": {"tool_name": name, "call_id": call_id}})
            audit = _start_tool_audit(
                db,
                agent_run_id=agent_run.id,
                tool_name=name,
                request_json={"arguments": args},
            )
            if tool is None:
                content_text = f"未知工具：{name}"
                _finish_tool_audit(db, audit, status="failed", response_json={}, error_message=content_text)
                emit({"event": "tool_call_failed", "data": {"tool_name": name, "call_id": call_id, "message": content_text}})
            else:
                try:
                    result = await tool.ainvoke(args)
                    content_text = _serialize_tool_result(result)
                    _finish_tool_audit(db, audit, status="ok", response_json=_serialize_any(result))
                    emit({"event": "tool_call_completed", "data": {"tool_name": name, "call_id": call_id}})
                except Exception as exc:
                    content_text = f"工具执行失败：{exc}"
                    _finish_tool_audit(db, audit, status="failed", response_json={}, error_message=str(exc))
                    emit({"event": "tool_call_failed", "data": {"tool_name": name, "call_id": call_id, "message": str(exc)}})
            tool_messages.append(
                {"role": "tool", "content": content_text, "name": name, "tool_call_id": call_id}
            )
            summaries.append(f"{name}: {content_text[:80]}")
    observation = "；".join(summaries)[:120] or "本轮无工具调用"
    completed_step = {**running_step, "content": observation, "status": "completed"}
    emit(_build_reasoning_step_event(assistant_message.id, completed_step))
    return {
        "tool_messages": tool_messages,
        "observation": observation,
        "pending_tool_calls": [],
        "steps": [completed_step],
    }


async def _reflect_step(
    db: Session,
    thread: ConversationThread,
    assistant_message: ConversationMessage,
    agent_run: AgentRun,
    *,
    state: dict[str, Any],
    emit: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    settings = get_settings()
    iteration = int(state.get("iteration_count") or 0)
    max_iter = int(state.get("max_iterations") or settings.agent_max_loop_iterations)
    existing = list(state.get("reasoning_steps") or [])
    seq = len(existing) + 1
    aimessage = state.get("current_aimessage") or {}
    had_tool_calls = bool(aimessage.get("tool_calls"))
    decision = "done"
    rationale = ""
    follow_up_prompt: str | None = None
    if not had_tool_calls:
        decision = "done"
        rationale = "本轮无工具调用，回答完整"
    elif iteration >= max_iter:
        decision = "done"
        rationale = f"已达最大迭代次数（{max_iter}）"
    else:
        try:
            result = await llm.ainvoke_structured(
                settings,
                schema=llm.ReflectDecision,
                system_prompt=(
                    "你是 Synora 的执行评估器。判断当前工具调用链是否已获得足够信息"
                    "回答用户。若已充分，is_complete=true；若还需继续行动，"
                    "is_complete=false 并给出最多一句话的 follow_up_prompt 作为下一步指引。"
                ),
                user_text=state.get("user_message") or "",
                operation="agent_reflect",
            )
            if result.is_complete:
                decision = "done"
                rationale = result.rationale or "信息已充分"
            else:
                decision = "continue"
                rationale = result.rationale or "需要继续行动"
                follow_up_prompt = result.follow_up_prompt
        except Exception:
            decision = "done"
            rationale = "评估失败，保守收尾"
    step = {
        "seq": seq,
        "step_type": "reflect",
        "label": "反思",
        "content": rationale or decision,
        "status": "completed",
        "iteration": iteration,
    }
    emit(_build_reasoning_step_event(assistant_message.id, step))
    return {
        "loop_decision": decision,
        "reflection": rationale,
        "follow_up_prompt": follow_up_prompt,
        "steps": [step],
    }


async def _process_schedule_intake(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    agent_run: AgentRun,
    *,
    text_content: str,
    attachment_ids: list[int],
    context: dict[str, Any],
    action_group_id: str | None = None,
    revision: int = 1,
) -> tuple[str, list[int], dict[str, Any] | None, list[dict[str, Any]]]:
    tool_events: list[dict[str, Any]] = []
    supersede_action_group_id = context.get("supersede_action_group_id")
    schedule_scope = _schedule_scope_from_action_group(action_group_id or supersede_action_group_id)
    parse_audit = _start_tool_audit(
        db,
        agent_run_id=agent_run.id,
        tool_name="parse_schedule_draft",
        request_json={
            "text_content": text_content,
            "attachment_ids": attachment_ids,
            "context": context,
        },
    )
    tool_events.append({"event": "tool_call_started", "data": {"tool_name": "parse_schedule_draft"}})
    tool_message, parsed = await invoke_synora_tool(
        "parse_schedule_draft",
        {
            "text_content": text_content,
            "attachment_ids": attachment_ids,
            "context": _tool_context(context, user_id=user_id),
        },
    )
    _finish_tool_audit(
        db,
        parse_audit,
        status="ok",
        response_json={"content": str(tool_message.content), "structured": parsed},
    )
    tool_events.append({"event": "tool_call_completed", "data": {"tool_name": "parse_schedule_draft"}})

    if parsed.get("status") == "error":
        raise ValueError(str(parsed.get("message") or "日程草稿解析失败。"))

    draft = ScheduleEventDraft.model_validate(parsed.get("draft") or {})
    draft_hash = str(parsed.get("draft_hash") or build_draft_hash(draft))
    missing_fields = list(parsed.get("missing_fields") or [])
    ambiguity_flags = list(parsed.get("ambiguity_flags") or [])
    evidence_digest = list(parsed.get("evidence_digest") or draft.evidence_digest)
    parse_confidence = float(parsed.get("parse_confidence") or draft.parse_confidence)

    action_group_id = action_group_id or mint_token()
    created_ids: list[int] = []
    approval_required: dict[str, Any] | None = None
    source_history = _normalize_source_history(draft.source_text)

    if missing_fields:
        final_text = "我已经整理出一条日程草稿，但还缺少关键信息。请先补充后再确认。"
        _mark_action_group_status(db, supersede_action_group_id, lifecycle_status="superseded", is_actionable=False)
        _upsert_pending_state(
            db,
            thread.id,
            user_id,
            pending_type="schedule",
            stage="needs_input",
            draft_hash=draft_hash,
            approval_token=None,
            attachment_ids=draft.source_attachment_ids,
            payload_json=draft.model_dump(mode="json", by_alias=True),
            meta_json={
                "missing_fields": missing_fields,
                "ambiguity_flags": ambiguity_flags,
                "evidence_digest": evidence_digest,
                "parse_confidence": parse_confidence,
                "action_group_id": action_group_id,
                "revision": revision,
                "source_history": source_history,
            },
        )
        card = _append_message(
            db,
            thread,
            role="assistant",
            message_type="schedule_draft_card",
            status="completed",
            text_content=None,
            action_group_id=action_group_id,
            revision=revision,
            structured_payload=_schedule_card_payload(
                draft=draft,
                missing_fields=missing_fields,
                ambiguity_flags=ambiguity_flags,
                evidence_digest=evidence_digest,
                parse_confidence=parse_confidence,
                stage="needs_input",
                actions=["submit_missing_fields", "dismiss_pending_action"],
                action_group_id=action_group_id,
                revision=revision,
                lifecycle_status="needs_input",
                is_actionable=True,
            ),
        )
        created_ids.append(card.id)
        return final_text, created_ids, None, tool_events

    conflict_audit = _start_tool_audit(
        db,
        agent_run_id=agent_run.id,
        tool_name="detect_schedule_conflicts",
        request_json={
            "draft": draft.model_dump(mode="json", by_alias=True),
            "draft_hash": draft_hash,
            "approval_scope": schedule_scope,
        },
    )
    tool_events.append({"event": "tool_call_started", "data": {"tool_name": "detect_schedule_conflicts"}})
    conflict_message, conflict_result = await invoke_synora_tool(
        "detect_schedule_conflicts",
        {
            "draft": draft.model_dump(mode="json", by_alias=True),
            "draft_hash": draft_hash,
            "context": _tool_context(context, user_id=user_id, approval_scope=schedule_scope),
        },
    )
    if conflict_result.get("status") == "error":
        raise ValueError(str(conflict_result.get("message") or "日程冲突检查失败。"))
    _finish_tool_audit(
        db,
        conflict_audit,
        status="ok",
        response_json={"content": str(conflict_message.content), "structured": conflict_result},
    )
    tool_events.append({"event": "tool_call_completed", "data": {"tool_name": "detect_schedule_conflicts"}})

    approval = dict(conflict_result.get("approval") or {})
    final_text = "我已经整理好日程草稿，并完成冲突检查。确认后我会正式创建日程和提醒。"
    _mark_action_group_status(db, supersede_action_group_id, lifecycle_status="superseded", is_actionable=False)
    _upsert_pending_state(
        db,
        thread.id,
        user_id,
        pending_type="schedule",
        stage="approval_pending",
        draft_hash=str(approval.get("draft_hash") or draft_hash),
        approval_token=str(approval.get("approval_token") or ""),
        attachment_ids=draft.source_attachment_ids,
        payload_json=draft.model_dump(mode="json", by_alias=True),
        meta_json={
            "conflict_items": list(conflict_result.get("conflict_items") or []),
            "suggestions": list(conflict_result.get("suggestions") or []),
            "risk_level": str(conflict_result.get("risk_level") or "low"),
            "evidence_digest": evidence_digest,
            "ambiguity_flags": ambiguity_flags,
            "parse_confidence": parse_confidence,
            "action_group_id": action_group_id,
            "revision": revision,
            "source_history": source_history,
        },
    )
    draft_card = _append_message(
        db,
        thread,
        role="assistant",
        message_type="schedule_draft_card",
        status="completed",
        text_content=None,
        action_group_id=action_group_id,
        revision=revision,
        structured_payload=_schedule_card_payload(
            draft=draft,
            missing_fields=[],
            ambiguity_flags=ambiguity_flags,
            evidence_digest=evidence_digest,
            parse_confidence=parse_confidence,
            stage="approval_pending",
            actions=["confirm_schedule_draft", "dismiss_pending_action"],
            action_group_id=action_group_id,
            revision=revision,
            lifecycle_status="approval_pending",
            is_actionable=True,
        ),
    )
    conflict_card = _append_message(
        db,
        thread,
        role="assistant",
        message_type="conflict_card",
        status="completed",
        text_content=None,
        action_group_id=action_group_id,
        revision=revision,
        structured_payload={
            "card_type": "conflict_check",
            "risk_level": str(conflict_result.get("risk_level") or "low"),
            "conflict_items": list(conflict_result.get("conflict_items") or []),
            "suggestions": list(conflict_result.get("suggestions") or []),
            "action_group_id": action_group_id,
            "revision": revision,
            "lifecycle_status": "conflict_review",
            "is_actionable": False,
        },
    )
    created_ids.extend([draft_card.id, conflict_card.id])
    approval_required = {
        "pending_type": "schedule",
        "stage": "approval_pending",
        "action_group_id": action_group_id,
        "approval_token": approval.get("approval_token"),
    }
    return final_text, created_ids, approval_required, tool_events



async def _process_quick_note_intake(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    agent_run: AgentRun,
    *,
    text_content: str,
    attachment_ids: list[int],
    context: dict[str, Any],
    action_group_id: str | None = None,
    revision: int = 1,
) -> tuple[str, list[int], dict[str, Any] | None, list[dict[str, Any]]]:
    tool_events: list[dict[str, Any]] = []
    supersede_action_group_id = context.get("supersede_action_group_id")
    action_group_id = action_group_id or mint_token()
    quick_note_scope = _quick_note_scope_from_action_group(action_group_id)
    tool_audit = _start_tool_audit(
        db,
        agent_run_id=agent_run.id,
        tool_name="prepare_quick_note_draft",
        request_json={
            "content": text_content,
            "tags": [],
            "attachment_ids": attachment_ids,
            "context": {
                **context,
                "approval_scope": quick_note_scope,
            },
        },
    )
    tool_events.append({"event": "tool_call_started", "data": {"tool_name": "prepare_quick_note_draft"}})
    tool_message, parsed = await invoke_synora_tool(
        "prepare_quick_note_draft",
        {
            "content": text_content,
            "tags": [],
            "attachment_ids": attachment_ids,
            "context": _tool_context(context, user_id=user_id, approval_scope=quick_note_scope),
        },
    )
    _finish_tool_audit(
        db,
        tool_audit,
        status="ok",
        response_json={"content": str(tool_message.content), "structured": parsed},
    )
    tool_events.append({"event": "tool_call_completed", "data": {"tool_name": "prepare_quick_note_draft"}})

    if parsed.get("status") == "error":
        raise ValueError(str(parsed.get("message") or "速记草稿生成失败。"))

    normalized_content = str(parsed.get("normalized_content") or "").strip()
    preview_tags = list(parsed.get("preview_tags") or [])
    approval = dict(parsed.get("approval") or {})
    evidence_digest = list(parsed.get("evidence_digest") or [])

    final_text = "我已经整理好这条速记，确认后就会正式保存。"
    _mark_action_group_status(db, supersede_action_group_id, lifecycle_status="superseded", is_actionable=False)
    _upsert_pending_state(
        db,
        thread.id,
        user_id,
        pending_type="quick_note",
        stage="approval_pending",
        draft_hash=str(approval.get("draft_hash") or ""),
        approval_token=str(approval.get("approval_token") or ""),
        attachment_ids=attachment_ids,
        payload_json={
            "content": normalized_content,
            "tags": preview_tags,
            "attachment_ids": attachment_ids,
        },
        meta_json={
            "evidence_digest": evidence_digest,
            "action_group_id": action_group_id,
            "revision": revision,
        },
    )
    card = _append_message(
        db,
        thread,
        role="assistant",
        message_type="quick_note_preview_card",
        status="completed",
        text_content=None,
        action_group_id=action_group_id,
        revision=revision,
        structured_payload={
            "card_type": "quick_note_preview",
            "normalized_content": normalized_content,
            "preview_tags": preview_tags,
            "evidence_digest": evidence_digest,
            "actions": ["confirm_quick_note", "dismiss_pending_action"],
            "action_group_id": action_group_id,
            "revision": revision,
            "lifecycle_status": "approval_pending",
            "is_actionable": True,
        },
    )
    return (
        final_text,
        [card.id],
        {
            "pending_type": "quick_note",
            "stage": "approval_pending",
            "action_group_id": action_group_id,
            "approval_token": approval.get("approval_token"),
        },
        tool_events,
    )


def _submit_schedule_missing_fields(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    pending: ConversationPendingState,
    payload: dict,
) -> list[ConversationMessage]:
    existing_draft = ScheduleEventDraft.model_validate(pending.payload_json)
    merged_payload = existing_draft.model_dump(mode="json", by_alias=True)
    if payload.get("title") is not None:
        merged_payload["title"] = payload["title"]
    if payload.get("location") is not None:
        merged_payload["location"] = payload["location"]
    if payload.get("details") is not None:
        merged_payload["details"] = payload["details"]
    if payload.get("start_at"):
        merged_payload["start"] = {"dateTime": payload["start_at"], "timeZone": existing_draft.start.time_zone}
    if payload.get("end_at"):
        merged_payload["end"] = {"dateTime": payload["end_at"], "timeZone": existing_draft.end.time_zone}
    if "reminder_preset" in payload:
        merged_payload["reminder_preset"] = normalize_reminder_preset(payload.get("reminder_preset"))

    updated_draft = ScheduleEventDraft.model_validate(merged_payload)
    missing_fields: list[str] = []
    if not updated_draft.title.strip():
        missing_fields.append("title")
    if not updated_draft.start.date_time:
        missing_fields.append("start_at")
    if not updated_draft.end.date_time:
        missing_fields.append("end_at")

    action_group_id = pending.meta_json.get("action_group_id") or mint_token()
    revision = int(pending.meta_json.get("revision") or 1) + 1
    _mark_action_group_status(db, action_group_id, lifecycle_status="superseded", is_actionable=False)
    source_history = _normalize_source_history(
        pending.meta_json.get("source_history"),
        updated_draft.source_text,
    )
    updated_draft = updated_draft.model_copy(update={"source_text": "\n\n".join(source_history)})

    if missing_fields:
        draft_hash = build_draft_hash(updated_draft)
        _upsert_pending_state(
            db,
            thread.id,
            user_id,
            pending_type="schedule",
            stage="needs_input",
            draft_hash=draft_hash,
            approval_token=None,
            attachment_ids=updated_draft.source_attachment_ids,
            payload_json=updated_draft.model_dump(mode="json", by_alias=True),
            meta_json={
                **pending.meta_json,
                "missing_fields": missing_fields,
                "action_group_id": action_group_id,
                "revision": revision,
                "source_history": source_history,
            },
        )
        return [
            _append_message(
                db,
                thread,
                role="assistant",
                message_type="schedule_draft_card",
                status="completed",
                text_content=None,
                action_group_id=action_group_id,
                revision=revision,
                structured_payload=_schedule_card_payload(
                    draft=updated_draft,
                    missing_fields=missing_fields,
                    ambiguity_flags=list(pending.meta_json.get("ambiguity_flags", [])),
                    evidence_digest=list(pending.meta_json.get("evidence_digest", [])),
                    parse_confidence=float(pending.meta_json.get("parse_confidence", 0)),
                    stage="needs_input",
                    actions=["submit_missing_fields", "dismiss_pending_action"],
                    action_group_id=action_group_id,
                    revision=revision,
                    lifecycle_status="needs_input",
                    is_actionable=True,
                ),
            )
        ]

    conflict_result = detect_conflicts(
        db,
        user_id,
        updated_draft,
        build_draft_hash(updated_draft),
        approval_scope=_schedule_scope_from_action_group(action_group_id),
    )
    _upsert_pending_state(
        db,
        thread.id,
        user_id,
        pending_type="schedule",
        stage="approval_pending",
        draft_hash=conflict_result.approval.draft_hash,
        approval_token=conflict_result.approval.approval_token,
        attachment_ids=updated_draft.source_attachment_ids,
        payload_json=updated_draft.model_dump(mode="json", by_alias=True),
        meta_json={
            **pending.meta_json,
            "conflict_items": [item.model_dump(mode="json", by_alias=True) for item in conflict_result.conflict_items],
            "suggestions": [item.model_dump(mode="json", by_alias=True) for item in conflict_result.suggestions],
            "risk_level": conflict_result.risk_level,
            "action_group_id": action_group_id,
            "revision": revision,
            "source_history": source_history,
        },
    )
    draft_card = _append_message(
        db,
        thread,
        role="assistant",
        message_type="schedule_draft_card",
        status="completed",
        text_content=None,
        action_group_id=action_group_id,
        revision=revision,
        structured_payload=_schedule_card_payload(
            draft=updated_draft,
            missing_fields=[],
            ambiguity_flags=list(pending.meta_json.get("ambiguity_flags", [])),
            evidence_digest=list(pending.meta_json.get("evidence_digest", [])),
            parse_confidence=float(pending.meta_json.get("parse_confidence", 0)),
            stage="approval_pending",
            actions=["confirm_schedule_draft", "dismiss_pending_action"],
            action_group_id=action_group_id,
            revision=revision,
            lifecycle_status="approval_pending",
            is_actionable=True,
        ),
    )
    conflict_card = _append_message(
        db,
        thread,
        role="assistant",
        message_type="conflict_card",
        status="completed",
        text_content=None,
        action_group_id=action_group_id,
        revision=revision,
        structured_payload={
            "card_type": "conflict_check",
            "risk_level": conflict_result.risk_level,
            "conflict_items": [item.model_dump(mode="json", by_alias=True) for item in conflict_result.conflict_items],
            "suggestions": [item.model_dump(mode="json", by_alias=True) for item in conflict_result.suggestions],
            "action_group_id": action_group_id,
            "revision": revision,
            "lifecycle_status": "conflict_review",
            "is_actionable": False,
        },
    )
    return [draft_card, conflict_card]



def _confirm_schedule_pending(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    pending: ConversationPendingState,
) -> list[ConversationMessage]:
    draft = ScheduleEventDraft.model_validate(pending.payload_json)
    action_group_id = pending.meta_json.get("action_group_id")
    schedule, jobs = create_schedule_after_approval(db, user_id, pending.approval_token or "", draft)
    try:
        _mark_action_group_status(
            db,
            action_group_id,
            lifecycle_status="confirmed",
            is_actionable=False,
            terminal_summary="已确认保存日程。",
            extra_updates={
                "confirmed_schedule": {
                    "schedule_id": schedule.id,
                    "title": schedule.title,
                    "source_text": getattr(schedule, "source_text", draft.source_text),
                    "details": schedule.details,
                    "start": {"dateTime": schedule.start_at.astimezone(ZoneInfo(schedule.time_zone)).isoformat(), "timeZone": schedule.time_zone},
                    "end": {"dateTime": schedule.end_at.astimezone(ZoneInfo(schedule.time_zone)).isoformat(), "timeZone": schedule.time_zone},
                    "channels": [job.channel for job in jobs],
                    "reminder_preset": getattr(schedule, "reminder_preset", draft.reminder_preset),
                },
            },
        )
        _clear_pending_state(db, pending)
    except Exception:
        logger.exception(
            "schedule_confirm_finalize_failed user_id=%s conversation_id=%s pending_id=%s action_group_id=%s schedule_id=%s stage=card_finalize",
            user_id,
            thread.id,
            pending.id,
            action_group_id,
            schedule.id,
        )
        try:
            db.rollback()
        except Exception:
            pass
    _enqueue_memory_writeback(
        user_id=user_id,
        source_kind="confirmed_schedule",
        source_ref_id=str(schedule.id),
        text=f"{schedule.title} {schedule.details}".strip(),
        summary="已确认日程",
    )
    return []


def _build_user_message_payload(
    db: Session,
    *,
    user_id: int,
    attachment_ids: list[int],
    selected_tool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if selected_tool:
        payload["selected_tool"] = selected_tool

    normalized_ids = [int(item) for item in attachment_ids if isinstance(item, int)]
    if not normalized_ids:
        return payload

    rows = db.scalars(
        select(Attachment)
        .where(Attachment.user_id == user_id, Attachment.id.in_(normalized_ids))
        .order_by(Attachment.id.asc())
    ).all()
    by_id = {row.id: row for row in rows}
    refs: list[dict[str, Any]] = []
    for attachment_id in normalized_ids:
        row = by_id.get(attachment_id)
        if row is None:
            continue
        refs.append(
            {
                "attachment_id": row.id,
                "file_name": row.file_name,
                "content_type": row.content_type,
                "size_bytes": row.size_bytes,
            }
        )
    if refs:
        payload["attachment_refs"] = refs
    return payload


def _confirm_quick_note_pending(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    pending: ConversationPendingState,
) -> list[ConversationMessage]:
    payload = dict(pending.payload_json)
    note = save_note_after_approval(
        db,
        user_id,
        content=str(payload.get("content") or ""),
        tags=list(payload.get("tags") or []),
        attachment_ids=list(payload.get("attachment_ids") or []),
        approval_token=pending.approval_token or "",
    )
    _mark_action_group_status(
        db,
        pending.meta_json.get("action_group_id"),
        lifecycle_status="confirmed",
        is_actionable=False,
        terminal_summary="已确认保存速记。",
        extra_updates={
            "confirmed_quick_note": {
                "note_id": note.id,
                "content": note.content,
                "tags": list(note.topic_tags_json),
            },
        },
    )
    _clear_pending_state(db, pending)
    write_user_memory.delay(
        user_id=user_id,
        source_kind="confirmed_quick_note",
        source_ref_id=str(note.id),
        text=note.content,
        summary="已确认速记",
    )
    return []


async def _emit_text_stream(db: Session, assistant_message: ConversationMessage, text: str) -> AsyncGenerator[dict, None]:
    aggregated = ""
    for index in range(0, len(text), 12):
        chunk = text[index : index + 12]
        aggregated += chunk
        assistant_message.text_content = aggregated
        db.commit()
        yield {"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": chunk}}
    assistant_message.status = "completed"
    db.commit()


async def _replay_completed_run(
    db: Session,
    agent_run: AgentRun,
    assistant_message: ConversationMessage,
) -> AsyncGenerator[dict, None]:
    assistant_text = str(agent_run.output_json.get("assistant_text") or assistant_message.text_content or "")
    yield {
        "event": "run_started",
        "data": {
            "assistant_message_id": assistant_message.id,
            "stream_id": agent_run.stream_token,
        },
    }
    if assistant_text:
        yield {"event": "message_completed", "data": {"message": _message_payload(assistant_message)}}
    for message_id in list(agent_run.output_json.get("created_message_ids") or []):
        message = db.get(ConversationMessage, message_id)
        if message:
            yield {"event": "card_snapshot", "data": {"message": _message_payload(message)}}
    yield {"event": "run_completed", "data": {"stream_id": agent_run.stream_token}}


def _finalize_run(
    db: Session,
    agent_run: AgentRun,
    assistant_message: ConversationMessage,
    *,
    assistant_text: str,
    created_message_ids: list[int],
) -> None:
    assistant_message.text_content = assistant_text
    assistant_message.status = "completed"
    agent_run.status = "completed"
    agent_run.stream_status = "completed"
    agent_run.output_json = {
        **dict(agent_run.output_json or {}),
        "assistant_text": assistant_text,
        "created_message_ids": created_message_ids,
    }
    agent_run.completed_at = datetime.now(timezone.utc)
    db.commit()
    if (assistant_text or "").strip():
        ConversationHistorySearchService().upsert_message(assistant_message)
    user_message = db.get(ConversationMessage, agent_run.user_message_id) if agent_run.user_message_id else None
    if user_message and (user_message.text_content or "").strip() and agent_run.workflow == "general_chat":
        memory_entries = MemoryService().extract_memory_facts(text=(user_message.text_content or "").strip(), summary=assistant_text[:200])
        if memory_entries:
            _enqueue_memory_writeback(
                user_id=agent_run.user_id,
                source_kind="conversation_message",
                source_ref_id=str(user_message.id),
                text=(user_message.text_content or "").strip(),
                summary=assistant_text[:200],
            )


def _mark_action_group_status(
    db: Session,
    action_group_id: str | None,
    *,
    lifecycle_status: str,
    is_actionable: bool,
    terminal_summary: str | None = None,
    extra_updates: dict[str, Any] | None = None,
) -> None:
    if not action_group_id:
        return
    messages = db.scalars(select(ConversationMessage).where(ConversationMessage.action_group_id == action_group_id)).all()
    for message in messages:
        payload = dict(message.structured_payload_json or {})
        payload["lifecycle_status"] = lifecycle_status
        payload["is_actionable"] = is_actionable
        payload["actions"] = []
        if terminal_summary:
            payload["terminal_summary"] = terminal_summary
        if extra_updates:
            payload.update(extra_updates)
        message.structured_payload_json = payload
        message.status = "completed"
    db.commit()


def _schedule_card_payload(
    *,
    draft: ScheduleEventDraft,
    missing_fields: list[str],
    ambiguity_flags: list[str],
    evidence_digest: list[str],
    parse_confidence: float,
    stage: str,
    actions: list[str],
    action_group_id: str,
    revision: int,
    lifecycle_status: str,
    is_actionable: bool,
) -> dict:
    return {
        "card_type": "schedule_draft",
        "draft": draft.model_dump(mode="json", by_alias=True),
        "missing_fields": missing_fields,
        "ambiguity_flags": ambiguity_flags,
        "evidence_digest": evidence_digest,
        "parse_confidence": parse_confidence,
        "stage": stage,
        "actions": actions,
        "action_group_id": action_group_id,
        "revision": revision,
        "lifecycle_status": lifecycle_status,
        "is_actionable": is_actionable,
    }


def _get_pending_state(db: Session, conversation_id: int) -> ConversationPendingState | None:
    return db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == conversation_id))


def _resolve_contextual_draft_followup(
    db: Session,
    conversation_id: int,
    text_content: str,
    context: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """无 pending 时，从最近一张未决草稿卡重建续接 context。

    兜底路径：若首条消息未正确进入 intake（历史路由遗漏），第二条“补充/修正”
    消息借助上一条 schedule_draft_card / quick_note_preview_card 重建与
    ``_prepare_pending_regeneration`` 同构的 context，驱动 parse_schedule_draft
    合并上一版草稿 + 本轮更正。命中返回 ``(intent, context)``，否则返回 None。
    """
    card = db.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "assistant",
            ConversationMessage.message_type.in_(["schedule_draft_card", "quick_note_preview_card"]),
        )
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(1)
    )
    if not card:
        return None
    payload = dict(card.structured_payload_json or {})
    if payload.get("lifecycle_status") not in {"needs_input", "approval_pending"}:
        return None
    action_group_id = str(payload.get("action_group_id") or "")
    if not action_group_id:
        return None
    revision = int(payload.get("revision") or 1)

    previous_context = dict(context)
    previous_context["pending_action_group_id"] = action_group_id
    previous_context["pending_revision"] = str(revision + 1)
    previous_context["supersede_action_group_id"] = action_group_id

    if card.message_type == "schedule_draft_card":
        try:
            draft = ScheduleEventDraft.model_validate(payload.get("draft") or {})
        except Exception:
            return None
        previous_context["pending_regeneration"] = "schedule"
        previous_context["source_history"] = _normalize_source_history(draft.source_text, text_content)
        previous_context["previous_draft_summary"] = _schedule_draft_summary(draft)
        return "schedule_intake", previous_context

    previous_context["pending_regeneration"] = "quick_note"
    previous_context["previous_note_content"] = str(payload.get("normalized_content") or "").strip()
    previous_context["latest_user_text"] = text_content.strip()
    return "quick_note_intake", previous_context


def _upsert_pending_state(
    db: Session,
    conversation_id: int,
    user_id: int,
    *,
    pending_type: str,
    stage: str,
    draft_hash: str | None,
    approval_token: str | None,
    attachment_ids: list[int],
    payload_json: dict,
    meta_json: dict,
    planned_at: datetime | None = None,
    intent_type: str | None = None,
) -> ConversationPendingState:
    pending = _get_pending_state(db, conversation_id)
    if not pending:
        pending = ConversationPendingState(
            conversation_id=conversation_id,
            user_id=user_id,
            pending_type=pending_type,
            stage=stage,
            draft_hash=draft_hash,
            approval_token=approval_token,
            source_type="mixed",
            attachment_ids_json=attachment_ids,
            payload_json=payload_json,
            meta_json=meta_json,
            planned_at=planned_at,
            intent_type=intent_type,
        )
        db.add(pending)
    else:
        pending.pending_type = pending_type
        pending.stage = stage
        pending.draft_hash = draft_hash
        pending.approval_token = approval_token
        pending.source_type = "mixed"
        pending.attachment_ids_json = attachment_ids
        pending.payload_json = payload_json
        pending.meta_json = meta_json
        pending.planned_at = planned_at
        pending.intent_type = intent_type
    db.commit()
    db.refresh(pending)
    return pending


def _clear_pending_state(db: Session, pending: ConversationPendingState) -> None:
    db.delete(pending)
    db.commit()


def mark_cross_day_intent(db: Session, conversation_id: int, *, planned_at: datetime) -> ConversationPendingState | None:
    """将挂起会话标记为跨天意图：到 ``planned_at`` 时由 beat 任务主动唤醒跟进。

    供意图路由在识别到“改天再处理”时调用；返回更新后的 pending，若该会话无
    挂起状态则返回 None（不做任何事）。
    """
    pending = _get_pending_state(db, conversation_id)
    if not pending:
        return None
    pending.intent_type = "cross_day"
    pending.planned_at = planned_at
    db.commit()
    db.refresh(pending)
    return pending


def _delete_approvals_for_action_groups(
    db: Session,
    *,
    user_id: int,
    action_group_ids: set[str],
    approval_token: str | None = None,
) -> None:
    scopes = {
        scope
        for action_group_id in action_group_ids
        for scope in (
            _schedule_scope_from_action_group(action_group_id),
            _quick_note_scope_from_action_group(action_group_id),
        )
        if scope
    }
    approvals_by_scope: list[ApprovalRequest] = []
    if scopes:
        approvals_by_scope = db.scalars(
            select(ApprovalRequest).where(
                ApprovalRequest.user_id == user_id,
                ApprovalRequest.approval_scope.in_(list(scopes)),
            )
        ).all()

    approvals: dict[int, ApprovalRequest] = {item.id: item for item in approvals_by_scope}
    normalized_token = str(approval_token or "").strip()
    if normalized_token:
        approval = db.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.user_id == user_id,
                ApprovalRequest.token_hash == sha256_text(normalized_token),
            )
        )
        if approval is not None:
            approvals[approval.id] = approval

    if not approvals:
        return
    for approval in approvals.values():
        db.delete(approval)
    db.commit()


def _append_message(
    db: Session,
    thread: ConversationThread,
    *,
    role: str,
    message_type: str,
    status: str,
    text_content: str | None,
    structured_payload: dict,
    action_group_id: str | None = None,
    revision: int = 1,
) -> ConversationMessage:
    message = ConversationMessage(
        conversation_id=thread.id,
        role=role,
        message_type=message_type,
        status=status,
        text_content=text_content,
        structured_payload_json=structured_payload,
        action_group_id=action_group_id,
        revision=revision,
    )
    thread.last_message_at = datetime.now(timezone.utc)
    db.add(message)
    db.commit()
    db.refresh(message)
    db.refresh(thread)
    return message


def _message_payload(message: ConversationMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "message_type": message.message_type,
        "status": message.status,
        "text_content": message.text_content,
        "structured_payload": dict(message.structured_payload_json or {}),
        "action_group_id": message.action_group_id,
        "revision": message.revision,
        "created_at": message.created_at.isoformat(),
    }


def _start_tool_audit(
    db: Session,
    *,
    agent_run_id: int,
    tool_name: str,
    request_json: dict[str, Any],
) -> AgentToolCallAudit:
    audit = AgentToolCallAudit(
        agent_run_id=agent_run_id,
        tool_name=tool_name,
        request_json=request_json,
        response_json={},
        status="running",
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


def _finish_tool_audit(
    db: Session,
    audit: AgentToolCallAudit | None,
    *,
    status: str,
    response_json: dict[str, Any],
    error_message: str | None = None,
) -> None:
    if audit is None:
        return
    audit.status = status
    audit.response_json = response_json
    audit.error_message = error_message
    db.commit()


def _serialize_any(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _serialize_any(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_serialize_any(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return str(value)


def _extract_langchain_delta(event: dict[str, Any]) -> str:
    chunk = event.get("data", {}).get("chunk")
    if chunk is None:
        return ""
    return llm.extract_message_text(chunk)


def _extract_langchain_final_text(event: dict[str, Any]) -> str:
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


def _prepare_pending_regeneration(
    db: Session,
    *,
    user_id: int,
    pending: ConversationPendingState,
    text_content: str,
    attachment_ids: list[int],
    attachment_parts: list[dict[str, Any]],
    context: dict[str, Any],
) -> tuple[str, str, list[int], list[dict[str, Any]], dict[str, Any]]:
    previous_attachment_ids = list(pending.attachment_ids_json or [])
    merged_attachment_ids: list[int] = []
    for attachment_id in previous_attachment_ids + list(attachment_ids):
        if attachment_id not in merged_attachment_ids:
            merged_attachment_ids.append(attachment_id)

    if merged_attachment_ids != list(attachment_ids):
        assets = build_attachment_prompt_assets(db, user_id=user_id, attachment_ids=merged_attachment_ids)
        attachment_parts = [part for asset in assets for part in asset.parts]

    previous_payload = dict(pending.payload_json or {})
    previous_context = dict(context)
    if pending.pending_type == "schedule":
        previous_draft = ScheduleEventDraft.model_validate(previous_payload)
        source_history = _normalize_source_history(
            pending.meta_json.get("source_history"),
            previous_draft.source_text,
            text_content,
        )
        merged_text = text_content.strip()
        previous_context["pending_regeneration"] = "schedule"
        previous_context["pending_action_group_id"] = str(pending.meta_json.get("action_group_id") or "")
        previous_context["pending_revision"] = str(int(pending.meta_json.get("revision") or 1) + 1)
        previous_context["supersede_action_group_id"] = str(pending.meta_json.get("action_group_id") or "")
        previous_context["source_history"] = source_history
        previous_context["previous_draft_summary"] = _schedule_draft_summary(previous_draft)
        return "schedule_intake", merged_text, merged_attachment_ids, attachment_parts, previous_context

    previous_content = str(previous_payload.get("content") or "").strip()
    merged_text = text_content.strip()
    previous_context["pending_regeneration"] = "quick_note"
    previous_context["pending_action_group_id"] = str(pending.meta_json.get("action_group_id") or "")
    previous_context["pending_revision"] = str(int(pending.meta_json.get("revision") or 1) + 1)
    previous_context["supersede_action_group_id"] = str(pending.meta_json.get("action_group_id") or "")
    previous_context["previous_note_content"] = previous_content
    previous_context["latest_user_text"] = text_content.strip()
    return "quick_note_intake", merged_text, merged_attachment_ids, attachment_parts, previous_context
