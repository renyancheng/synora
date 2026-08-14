"""会话领域编排服务。

只负责会话 CRUD、消息排队、SSE 流编排（legacy / LangGraph 双路径调度与收尾）、
历史召回和卡片动作分发。Agent 执行步骤（plan/act/observe/reflect）、日程与速记
intake 工作流、待确认状态与卡片生命周期、流运行时能力分别由
``agent_service`` / ``intake_service`` / ``pending_service`` / ``stream_runtime``
提供，legacy 与 LangGraph 路径共用这些公开服务，保证行为一致。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent import llm
from app.config import get_settings
from app.domains.attachment.service import build_attachment_prompt_assets
from app.domains.conversation.agent_service import RECENT_MESSAGE_DB_WINDOW, stream_general_chat
from app.domains.conversation.history_search_service import ConversationHistorySearchService
from app.domains.conversation.intake_service import process_quick_note_intake, process_schedule_intake
from app.domains.conversation.pending_service import (
    build_user_message_payload,
    clear_pending_state,
    confirm_quick_note_pending,
    confirm_schedule_pending,
    delete_approvals_for_action_groups,
    get_pending_state,
    mark_action_group_status,
    prepare_pending_regeneration,
    resolve_contextual_draft_followup,
    submit_schedule_missing_fields,
)
from app.domains.conversation.stream_runtime import (
    AgentRunCancelled,
    abort_stream,
    append_message,
    clear_stream_cancelled,
    emit_text_stream,
    enqueue_memory_writeback,
    message_payload,
    raise_if_stream_cancelled,
    summarize_reasoning_steps,
)
from app.domains.memory.service import MemoryService
from app.domains.schedule.service import normalize_reminder_preset
from app.models import AgentRun, ApprovalRequest, ConversationMessage, ConversationPendingState, ConversationThread
from app.runtime.errors import LLMServiceError
from app.schemas.conversation import ConversationActionRequest, ConversationSendMessageRequest
from app.security import mint_token

logger = logging.getLogger(__name__)

NON_EDITABLE_CARD_MESSAGE_TYPES = {
    "schedule_draft_card",
    "quick_note_preview_card",
    "conflict_card",
}
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

DEFAULT_THREAD_TITLE = "新对话"

# 空回答兜底：模型多次重试仍无文本时，以明确文案收口，避免前端出现空气泡。
EMPTY_ANSWER_FALLBACK_TEXT = "抱歉，这次没有生成有效回答，请点击“重新生成”或稍后再试。"


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
    pending = get_pending_state(db, conversation_id)
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
    delete_approvals_for_action_groups(
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

    pending = get_pending_state(db, conversation_id)
    if pending is not None:
        delete_approvals_for_action_groups(
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

    # 收尾副作用（记忆/向量索引清理）只做尽力而为：主事务已提交，
    # 清理失败记录结构化日志即可，不得让撤回请求返回 500。
    try:
        memory_service.delete_records_by_source(
            db,
            user_id=user_id,
            source_kind="conversation_message",
            source_ref_id=str(user_message.id),
        )
    except Exception:
        logger.warning(
            "rewind_memory_cleanup_failed conversation_id=%s",
            conversation_id,
            exc_info=True,
        )
    try:
        ConversationHistorySearchService().delete_messages(
            conversation_id=conversation_id,
            message_ids=deleted_text_message_ids,
        )
    except Exception:
        logger.warning(
            "rewind_history_index_cleanup_failed conversation_id=%s",
            conversation_id,
            exc_info=True,
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
    user_message = append_message(
        db,
        thread,
        role="user",
        message_type="text",
        status="sent",
        text_content=text_content,
        structured_payload=build_user_message_payload(
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

    assistant_message = append_message(
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


def _claim_pending_stream(db: Session, agent_run_id: int) -> bool:
    """原子抢占待消费流，确保同一 stream 仅有一个消费者可以开始执行。"""
    result = db.execute(
        update(AgentRun)
        .where(
            AgentRun.id == agent_run_id,
            AgentRun.stream_status == "pending",
        )
        .values(stream_status="active")
    )
    db.commit()
    return result.rowcount == 1


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

    if agent_run.stream_status == "completed":
        async for item in _replay_completed_run(db, agent_run, assistant_message):
            yield item
        return

    if agent_run.stream_status == "cancelling":
        assistant_message.status = "completed"
        agent_run.status = "cancelled"
        agent_run.stream_status = "cancelled"
        agent_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        clear_stream_cancelled(stream_id)
        yield {
            "event": "run_cancelled",
            "data": {
                "assistant_message_id": assistant_message.id,
                "stream_id": stream_id,
                "stream_status": "cancelled",
            },
        }
        return

    if not _claim_pending_stream(db, agent_run.id):
        # 同一 stream 的并发消费者可能已在本次读取后完成抢占，必须以数据库
        # 当前状态为准，不能依赖当前 Session 中可能过期的 ORM 实例。
        db.refresh(agent_run)
        if agent_run.stream_status == "completed":
            async for item in _replay_completed_run(db, agent_run, assistant_message):
                yield item
            return
        if agent_run.stream_status == "active":
            raise ValueError("这条消息正在生成中，请稍后再试。")
        raise ValueError("这条消息无法开始生成，请新建消息后重试。")

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
    except AgentRunCancelled as exc:
        # 发送中停止：保留已流出的文本，run_cancelled 收口，不视为失败
        assistant_message.status = "completed"
        agent_run.status = "cancelled"
        agent_run.stream_status = "cancelled"
        agent_run.completed_at = datetime.now(timezone.utc)
        agent_run.output_json = {**dict(agent_run.output_json or {}), "completion_status": "cancelled"}
        db.commit()
        clear_stream_cancelled(exc.stream_id)
        yield {
            "event": "run_cancelled",
            "data": {
                "assistant_message_id": assistant_message.id,
                "stream_id": stream_id,
                "stream_status": "cancelled",
            },
        }
    except Exception as exc:
        agent_run.status = "failed"
        agent_run.stream_status = "failed"
        agent_run.error_message = exc.message if isinstance(exc, LLMServiceError) else str(exc)
        agent_run.completed_at = datetime.now(timezone.utc)
        assistant_message.status = "failed"
        agent_run.output_json = {**dict(agent_run.output_json or {}), "completion_status": "failed"}
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
    pending = get_pending_state(db, conversation_id)
    if pending:
        intent, text_content, attachment_ids, attachment_parts, context = prepare_pending_regeneration(
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
            resolved = resolve_contextual_draft_followup(db, conversation_id, text_content, context)
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
        async for item in stream_general_chat(
            db,
            thread,
            assistant_message,
            agent_run,
            user_message=text_content,
            attachment_parts=attachment_parts,
            conversation_history_lines=list(context.get("conversation_history_lines") or []),
            stream_id=stream_id,
        ):
            yield item
        final_text = assistant_message.text_content or ""
        degradations: list[dict] = []
        if not final_text.strip():
            final_text = EMPTY_ANSWER_FALLBACK_TEXT
            degradations.append({"operation": "empty_answer_fallback", "reason": "empty_stream"})
        _finalize_run(db, agent_run, assistant_message, assistant_text=final_text, created_message_ids=[], degradations=degradations)
        yield {"event": "message_completed", "data": {"message": message_payload(assistant_message)}}
        yield {"event": "run_completed", "data": {"stream_id": stream_id}}
        return

    if intent == "schedule_intake":
        final_text, created_ids, requires_approval, tool_events = await process_schedule_intake(
            db,
            user_id,
            thread,
            agent_run,
            text_content=text_content,
            attachment_ids=attachment_ids,
            context=context,
            action_group_id=context.get("pending_action_group_id") or None,
            revision=int(context.get("pending_revision") or 1),
            stream_id=stream_id,
        )
    else:
        final_text, created_ids, requires_approval, tool_events = await process_quick_note_intake(
            db,
            user_id,
            thread,
            agent_run,
            text_content=text_content,
            attachment_ids=attachment_ids,
            context=context,
            action_group_id=context.get("pending_action_group_id") or None,
            revision=int(context.get("pending_revision") or 1),
            stream_id=stream_id,
        )

    for tool_event in tool_events:
        yield tool_event
    # 卡片前置说明：先发一句感知说明，再输出最终文案（emit_text_stream 追加语义）
    preamble = (
        "我注意到你想安排日程，我来整理一下。"
        if intent == "schedule_intake"
        else "我帮你记一条速记。"
    )
    assistant_message.text_content = (assistant_message.text_content or "") + preamble
    db.commit()
    yield {"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": preamble}}
    async for item in emit_text_stream(db, assistant_message, final_text, stream_id=stream_id):
        yield item
    _finalize_run(db, agent_run, assistant_message, assistant_text=assistant_message.text_content or "", created_message_ids=created_ids)
    yield {"event": "message_completed", "data": {"message": message_payload(assistant_message)}}
    for message_id in created_ids:
        message = db.get(ConversationMessage, message_id)
        if message:
            yield {"event": "card_snapshot", "data": {"message": message_payload(message)}}
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
    thread_id = agent_run.checkpoint_thread_id or f"agent_run_{agent_run.id}"
    agent_run.checkpoint_thread_id = thread_id
    db.commit()
    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }
    final_update: dict[str, Any] = {}
    async for mode, chunk in graph.astream(initial, config, stream_mode=["updates", "custom"]):
        # 图事件边界检查持久化取消状态，跨 worker 的 abort 也会在此收口。
        raise_if_stream_cancelled(db, stream_id)
        if mode == "custom":
            yield chunk
        elif isinstance(chunk, dict):
            final_update = chunk.get("finalize") or {}

    # 图节点使用短生命周期 Session；外层流 Session 必须刷新，避免以过期 ORM
    # 实例覆盖节点已提交的文本、卡片或 run 状态。
    db.refresh(agent_run)
    db.refresh(assistant_message)
    final_text = str(final_update.get("assistant_text") or "")
    created_ids = list(final_update.get("created_message_ids") or [])
    requires_approval = final_update.get("requires_approval")
    # 推理轨迹持久化：reasoning_step 消息卡片（历史可回溯），经 card_snapshot 推送。
    # 必须在 _finalize_run 之前创建，使其进入 created_message_ids：
    # 撤回/重发（rewind_last_turn）时才能随本轮一并删除，避免残留孤儿消息。
    reasoning_steps = list(final_update.get("reasoning_steps") or [])
    degradations = [
        {
            "operation": str(step.get("step_type") or "agent_step"),
            "reason": str(step.get("content") or ""),
        }
        for step in reasoning_steps
        if step.get("degraded") is True
    ]
    if not final_text.strip():
        # 空回答兜底：多次重试仍无文本时以明确文案收口，避免前端空气泡。
        final_text = EMPTY_ANSWER_FALLBACK_TEXT
        degradations.append({"operation": "empty_answer_fallback", "reason": "retries_exhausted"})
    if reasoning_steps:
        step_summary = summarize_reasoning_steps(reasoning_steps)
        step_message = append_message(
            db,
            thread,
            role="assistant",
            message_type="reasoning_step",
            status="completed",
            text_content=step_summary,
            structured_payload={"steps": reasoning_steps, "summary": step_summary},
        )
        created_ids.append(step_message.id)
    _finalize_run(db, agent_run, assistant_message, assistant_text=final_text, created_message_ids=created_ids, degradations=degradations)
    yield {"event": "message_completed", "data": {"message": message_payload(assistant_message)}}
    for message_id in created_ids:
        message = db.get(ConversationMessage, message_id)
        if message:
            yield {"event": "card_snapshot", "data": {"message": message_payload(message)}}
    if requires_approval:
        yield {"event": "approval_required", "data": requires_approval}
    yield {"event": "run_completed", "data": {"stream_id": stream_id}}


async def resume_stream_from_checkpoint(
    db: Session,
    user_id: int,
    conversation_id: int,
    stream_id: str,
) -> AsyncGenerator[dict, None]:
    """仅恢复已持久化到 finalize 前的安全 checkpoint，避免重复模型和工具副作用。"""
    from app.agent.checkpointer import setup_checkpointer
    from app.agent.graph import build_graph

    thread = get_conversation(db, user_id, conversation_id)
    agent_run = db.scalar(
        select(AgentRun).where(
            AgentRun.user_id == user_id,
            AgentRun.conversation_id == thread.id,
            AgentRun.stream_token == stream_id,
        )
    )
    if not agent_run:
        raise ValueError("会话流不存在。")
    if agent_run.stream_status == "completed":
        assistant_message = db.get(ConversationMessage, agent_run.assistant_message_id)
        if not assistant_message:
            raise ValueError("会话流消息不存在。")
        async for item in _replay_completed_run(db, agent_run, assistant_message):
            yield item
        return
    if agent_run.stream_status == "active":
        agent_run.status = "failed"
        agent_run.stream_status = "failed"
        agent_run.error_message = "运行中断，恢复该节点可能重复执行工具，已安全终止。"
        agent_run.completed_at = datetime.now(timezone.utc)
        agent_run.output_json = {**dict(agent_run.output_json or {}), "completion_status": "failed"}
        db.commit()
        raise ValueError("运行中断位置可能重复执行工具，已安全终止，请新建消息后重试。")
    if agent_run.stream_status != "failed" or not agent_run.checkpoint_thread_id:
        raise ValueError("该会话流当前不可恢复，请新建消息后重试。")

    await setup_checkpointer()
    checkpoint_config = {"configurable": {"thread_id": agent_run.checkpoint_thread_id}}
    graph = build_graph()
    snapshot = await graph.aget_state(checkpoint_config)
    next_nodes = tuple(snapshot.next or ())
    if next_nodes != ("finalize",):
        raise ValueError("该会话流中断位置可能重复执行工具，已拒绝恢复，请新建消息后重试。")

    assistant_message = db.get(ConversationMessage, agent_run.assistant_message_id)
    if not assistant_message:
        raise ValueError("会话流消息不存在。")
    final_update: dict[str, Any] = {}
    async for mode, chunk in graph.astream(None, checkpoint_config, stream_mode=["updates", "custom"]):
        if mode == "custom":
            yield chunk
        elif isinstance(chunk, dict):
            final_update = chunk.get("finalize") or {}

    final_text = str(final_update.get("assistant_text") or assistant_message.text_content or "")
    created_ids = list(final_update.get("created_message_ids") or [])
    _finalize_run(db, agent_run, assistant_message, assistant_text=final_text, created_message_ids=created_ids)
    yield {"event": "message_completed", "data": {"message": message_payload(assistant_message)}}
    yield {"event": "run_completed", "data": {"stream_id": stream_id}}


def apply_action(
    db: Session,
    user_id: int,
    conversation_id: int,
    payload: ConversationActionRequest,
) -> tuple[ConversationThread, list[ConversationMessage]]:
    thread = get_conversation(db, user_id, conversation_id)
    pending = get_pending_state(db, conversation_id)
    if not pending:
        raise ValueError("当前没有待处理的卡片操作。")

    action = payload.action
    if action == "dismiss_pending_action":
        mark_action_group_status(
            db,
            pending.meta_json.get("action_group_id"),
            lifecycle_status="cancelled",
            is_actionable=False,
            terminal_summary="已取消本次待确认操作。",
        )
        clear_pending_state(db, pending)
        return thread, []

    if action == "submit_missing_fields":
        if pending.pending_type != "schedule":
            raise ValueError("当前卡片不支持补充字段。")
        return thread, submit_schedule_missing_fields(db, user_id, thread, pending, payload.payload)

    if action == "confirm_schedule_draft":
        if pending.pending_type != "schedule" or pending.stage != "approval_pending":
            raise ValueError("当前没有可确认的日程草稿。")
        reminder_preset = payload.payload.get("reminder_preset")
        if reminder_preset:
            draft_payload = dict(pending.payload_json or {})
            draft_payload["reminder_preset"] = normalize_reminder_preset(reminder_preset)
            pending.payload_json = draft_payload
            db.commit()
        return thread, confirm_schedule_pending(db, user_id, thread, pending)

    if action == "confirm_quick_note":
        if pending.pending_type != "quick_note" or pending.stage != "approval_pending":
            raise ValueError("当前没有可确认的速记草稿。")
        return thread, confirm_quick_note_pending(db, user_id, thread, pending)

    raise ValueError("不支持的对话动作。")


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
        yield {"event": "message_completed", "data": {"message": message_payload(assistant_message)}}
    for message_id in list(agent_run.output_json.get("created_message_ids") or []):
        message = db.get(ConversationMessage, message_id)
        if message:
            yield {"event": "card_snapshot", "data": {"message": message_payload(message)}}
    yield {"event": "run_completed", "data": {"stream_id": agent_run.stream_token}}


def _finalize_run(
    db: Session,
    agent_run: AgentRun,
    assistant_message: ConversationMessage,
    *,
    assistant_text: str,
    created_message_ids: list[int],
    degradations: list[dict] | None = None,
) -> None:
    assistant_message.text_content = assistant_text
    assistant_message.status = "completed"
    agent_run.status = "completed"
    agent_run.stream_status = "completed"
    # completion_status 区分正常完成 / 降级完成；取消与失败由流编排层标记。
    normalized_degradations = [
        dict(item) for item in (degradations or []) if isinstance(item, dict) and item.get("operation")
    ]
    agent_run.output_json = {
        **dict(agent_run.output_json or {}),
        "assistant_text": assistant_text,
        "created_message_ids": created_message_ids,
        "completion_status": "degraded" if normalized_degradations else "completed",
        "degradations": normalized_degradations,
    }
    agent_run.completed_at = datetime.now(timezone.utc)
    db.commit()
    if (assistant_text or "").strip():
        ConversationHistorySearchService().upsert_message(assistant_message)
    user_message = db.get(ConversationMessage, agent_run.user_message_id) if agent_run.user_message_id else None
    if user_message and (user_message.text_content or "").strip() and agent_run.workflow == "general_chat":
        memory_entries = MemoryService().extract_memory_facts(text=(user_message.text_content or "").strip(), summary=assistant_text[:200])
        if memory_entries:
            enqueue_memory_writeback(
                user_id=agent_run.user_id,
                source_kind="conversation_message",
                source_ref_id=str(user_message.id),
                text=(user_message.text_content or "").strip(),
                summary=assistant_text[:200],
                agent_run_id=agent_run.id,
                conversation_id=agent_run.conversation_id,
            )


__all__ = [
    "AgentRunCancelled",
    "abort_stream",
    "apply_action",
    "consume_stream",
    "create_conversation",
    "delete_conversation",
    "get_conversation",
    "list_conversations",
    "list_messages",
    "queue_message",
    "resume_stream_from_checkpoint",
    "rewind_last_turn",
    "update_conversation_title",
]
