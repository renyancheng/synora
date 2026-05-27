from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.attachment.service import build_attachment_prompt_assets
from app.domains.memory.service import MemoryService
from app.domains.quick_note.service import save_note_after_approval
from app.domains.schedule.service import build_draft_hash, create_schedule_after_approval, detect_conflicts
from app.models import AgentRun, AgentToolCallAudit, ApprovalRequest, Attachment, ConversationMessage, ConversationPendingState, ConversationThread
from app.runtime.context_assembler import ContextAssembler
from app.runtime.errors import LLMServiceError
from app.runtime.mcp_client import get_synora_tools, invoke_synora_tool
from app.runtime.model_adapter import ModelAdapter
from app.schemas.conversation import ConversationActionRequest, ConversationSendMessageRequest
from app.schemas.quick_note import QuickNoteDraftRequest
from app.schemas.schedule import ScheduleEventDraft
from app.security import mint_token, sha256_text
from app.tasks.memory import write_user_memory


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



def _error_payload(exc: Exception, *, assistant_message_id: int) -> dict[str, object]:
    if isinstance(exc, LLMServiceError):
        return {
            "assistant_message_id": assistant_message_id,
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
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
    if agent_runs:
        run_ids = [item.id for item in agent_runs]
        audits = db.scalars(select(AgentToolCallAudit).where(AgentToolCallAudit.agent_run_id.in_(run_ids))).all()
        for audit in audits:
            db.delete(audit)
        for run in agent_runs:
            db.delete(run)
    db.delete(thread)
    db.commit()


def rewind_last_turn(db: Session, user_id: int, conversation_id: int) -> tuple[ConversationThread, ConversationMessage]:
    thread = get_conversation(db, user_id, conversation_id)
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
    if agent_run is not None:
        created_message_ids = [int(item) for item in list(agent_run.output_json.get("created_message_ids") or []) if isinstance(item, int)]
        assistant_message_id = agent_run.assistant_message_id
        audits = db.scalars(select(AgentToolCallAudit).where(AgentToolCallAudit.agent_run_id == agent_run.id)).all()
        for audit in audits:
            db.delete(audit)
        db.delete(agent_run)

    for message_id in created_message_ids:
        message = db.get(ConversationMessage, message_id)
        if message is not None:
            db.delete(message)

    if assistant_message_id is not None:
        assistant_message = db.get(ConversationMessage, assistant_message_id)
        if assistant_message is not None:
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

    memory_service.delete_records_by_source(
        db,
        user_id=user_id,
        source_kind="conversation_message",
        source_ref_id=str(user_message.id),
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
    if has_user_message is None and text_content:
        thread.title = ModelAdapter().generate_conversation_title(text_content)
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
        pending = _get_pending_state(db, conversation_id)
        model = ModelAdapter()
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
            intent = await model.aroute_conversation_intent(
                {
                    "text_content": text_content,
                    "attachment_ids": attachment_ids,
                    "selected_tool": selected_tool,
                    "context": context,
                },
                attachment_parts=attachment_parts,
            )
        agent_run.workflow = intent
        agent_run.output_json = {
            **dict(agent_run.output_json or {}),
            "workflow": intent,
            "model_name": model._settings.llm_model,
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
    except Exception as exc:
        agent_run.status = "failed"
        agent_run.stream_status = "failed"
        agent_run.error_message = exc.message if isinstance(exc, LLMServiceError) else str(exc)
        agent_run.completed_at = datetime.now(timezone.utc)
        assistant_message.status = "failed"
        db.commit()
        yield {"event": "run_failed", "data": _error_payload(exc, assistant_message_id=assistant_message.id)}


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
        _mark_action_group_status(db, pending.meta_json.get("action_group_id"), lifecycle_status="cancelled", is_actionable=False)
        _clear_pending_state(db, pending)
        message = _append_message(
            db,
            thread,
            role="assistant",
            message_type="result_card",
            status="completed",
            text_content="已取消本次待确认操作。",
            structured_payload={
                "card_type": "result",
                "result_kind": "action_cancelled",
                "summary": "已取消本次待确认操作。",
            },
        )
        return thread, [message]

    if action == "submit_missing_fields":
        if pending.pending_type != "schedule":
            raise ValueError("当前卡片不支持补充字段。")
        return thread, _submit_schedule_missing_fields(db, user_id, thread, pending, payload.payload)

    if action == "confirm_schedule_draft":
        if pending.pending_type != "schedule" or pending.stage != "approval_pending":
            raise ValueError("当前没有可确认的日程草稿。")
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
) -> AsyncGenerator[dict, None]:
    recent_messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == thread.id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(12)
    ).all()
    ordered = list(reversed(list(recent_messages)))
    model = ModelAdapter()
    tools = await get_synora_tools()
    agent = model.build_general_chat_agent(tools)
    memory_context = MemoryService().retrieve_context(
        db,
        user_id=thread.user_id,
        query_text=user_message,
    )
    memory_text = ContextAssembler.build_memory_context(
        memory_summary=memory_context.summary,
        memory_items=memory_context.items,
    )
    messages = model.build_langchain_messages(
        recent_messages=[
            {"role": item.role, "content": item.text_content or ""}
            for item in ordered
            if item.id != assistant_message.id and item.text_content
        ],
        user_message=f"{memory_text}\n\n当前输入：\n{user_message}".strip() if memory_text else user_message,
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


async def _process_schedule_intake(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    agent_run: AgentRun,
    *,
    text_content: str,
    attachment_ids: list[int],
    context: dict[str, str],
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
            "context": context,
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
    conflict_result_model = detect_conflicts(
        db,
        user_id,
        draft,
        draft_hash,
        approval_scope=schedule_scope,
    )
    conflict_result = {
        "status": conflict_result_model.status,
        "conflict_items": [item.model_dump(mode="json", by_alias=True) for item in conflict_result_model.conflict_items],
        "suggestions": [item.model_dump(mode="json", by_alias=True) for item in conflict_result_model.suggestions],
        "risk_level": conflict_result_model.risk_level,
        "approval": {
            "approval_token": conflict_result_model.approval.approval_token,
            "action": conflict_result_model.approval.action,
            "expires_at": conflict_result_model.approval.expires_at.isoformat(),
            "draft_hash": conflict_result_model.approval.draft_hash,
        },
    }
    _finish_tool_audit(
        db,
        conflict_audit,
        status="ok",
        response_json={"content": "detect_conflicts", "structured": conflict_result},
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
    context: dict[str, str],
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
            "context": {
                **context,
                "approval_scope": quick_note_scope,
            },
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
    schedule, jobs = create_schedule_after_approval(db, user_id, pending.approval_token or "", draft)
    _mark_action_group_status(db, pending.meta_json.get("action_group_id"), lifecycle_status="completed", is_actionable=False)
    _clear_pending_state(db, pending)
    schedule_source_text = getattr(schedule, "source_text", draft.source_text)
    message = _append_message(
        db,
        thread,
        role="assistant",
        message_type="result_card",
        status="completed",
        text_content="日程已创建并安排提醒。",
        structured_payload={
            "card_type": "result",
            "result_kind": "schedule_saved",
            "summary": "日程已创建并安排提醒。",
            "title": schedule.title,
            "source_text": schedule_source_text,
            "details": schedule.details,
            "start": {"dateTime": schedule.start_at.astimezone(ZoneInfo(schedule.time_zone)).isoformat(), "timeZone": schedule.time_zone},
            "end": {"dateTime": schedule.end_at.astimezone(ZoneInfo(schedule.time_zone)).isoformat(), "timeZone": schedule.time_zone},
            "channels": [job.channel for job in jobs],
        },
    )
    write_user_memory.delay(
        user_id=user_id,
        source_kind="confirmed_schedule",
        source_ref_id=str(schedule.id),
        text=f"{schedule.title} {schedule.details}".strip(),
        summary="已确认日程",
    )
    return [message]



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
    _mark_action_group_status(db, pending.meta_json.get("action_group_id"), lifecycle_status="completed", is_actionable=False)
    _clear_pending_state(db, pending)
    message = _append_message(
        db,
        thread,
        role="assistant",
        message_type="result_card",
        status="completed",
        text_content="速记已保存。",
        structured_payload={
            "card_type": "result",
            "result_kind": "quick_note_saved",
            "summary": "速记已保存。",
            "content": note.content,
            "tags": list(note.topic_tags_json),
        },
    )
    write_user_memory.delay(
        user_id=user_id,
        source_kind="confirmed_quick_note",
        source_ref_id=str(note.id),
        text=note.content,
        summary="已确认速记",
    )
    return [message]


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
) -> None:
    if not action_group_id:
        return
    messages = db.scalars(select(ConversationMessage).where(ConversationMessage.action_group_id == action_group_id)).all()
    for message in messages:
        payload = dict(message.structured_payload_json or {})
        payload["lifecycle_status"] = lifecycle_status
        payload["is_actionable"] = is_actionable
        payload["actions"] = []
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
    db.commit()
    db.refresh(pending)
    return pending


def _clear_pending_state(db: Session, pending: ConversationPendingState) -> None:
    db.delete(pending)
    db.commit()


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
    return ModelAdapter._extract_message_text(chunk)


def _extract_langchain_final_text(event: dict[str, Any]) -> str:
    output = event.get("data", {}).get("output")
    if isinstance(output, dict):
        messages = output.get("messages")
        if isinstance(messages, list) and messages:
            return ModelAdapter._extract_message_text(messages[-1])
        if isinstance(output.get("output"), str):
            return str(output.get("output")).strip()
        return ""
    if isinstance(output, list) and output:
        return ModelAdapter._extract_message_text(output[-1])
    return ModelAdapter._extract_message_text(output)


def _prepare_pending_regeneration(
    db: Session,
    *,
    user_id: int,
    pending: ConversationPendingState,
    text_content: str,
    attachment_ids: list[int],
    attachment_parts: list[dict[str, Any]],
    context: dict[str, str],
) -> tuple[str, str, list[int], list[dict[str, Any]], dict[str, str]]:
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
    base_text_parts = [
        "你正在修改同一条待确认速记。",
        f"上一版速记：{previous_content}",
        f"本轮补充或修正：{text_content}",
    ]
    merged_text = "\n".join(part for part in base_text_parts if part.strip())
    previous_context["pending_regeneration"] = "quick_note"
    previous_context["pending_action_group_id"] = str(pending.meta_json.get("action_group_id") or "")
    previous_context["pending_revision"] = str(int(pending.meta_json.get("revision") or 1) + 1)
    previous_context["supersede_action_group_id"] = str(pending.meta_json.get("action_group_id") or "")
    return "quick_note_intake", merged_text, merged_attachment_ids, attachment_parts, previous_context
