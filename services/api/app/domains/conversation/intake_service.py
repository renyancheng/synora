"""日程与速记 intake 工作流服务。

承载 general_chat 之外的草稿创建路径：解析日程草稿、冲突检查、生成速记预览，
统一产出（最终文案、创建的消息 ID、审批信息、工具事件）。legacy 路径与
LangGraph 的 schedule_intake / quick_note_intake 节点共用本模块，保证卡片、
pending 状态与审批副作用在两条路径下行为一致。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.domains.conversation.pending_service import (
    mark_action_group_status,
    normalize_source_history,
    quick_note_scope_from_action_group,
    schedule_card_payload,
    schedule_scope_from_action_group,
    tool_context,
    upsert_pending_state,
)
from app.domains.conversation.stream_runtime import (
    append_message,
    finish_tool_audit,
    raise_if_stream_cancelled,
    start_tool_audit,
)
from app.domains.schedule.service import build_draft_hash
from app.models import AgentRun, ConversationThread
from app.runtime.mcp_client import invoke_synora_tool
from app.schemas.schedule import ScheduleEventDraft
from app.security import mint_token


async def process_schedule_intake(
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
    stream_id: str | None = None,
) -> tuple[str, list[int], dict[str, Any] | None, list[dict[str, Any]]]:
    tool_events: list[dict[str, Any]] = []
    supersede_action_group_id = context.get("supersede_action_group_id")
    schedule_scope = schedule_scope_from_action_group(action_group_id or supersede_action_group_id)
    parse_audit = start_tool_audit(
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
    raise_if_stream_cancelled(db, stream_id, force_database_check=True)
    tool_message, parsed = await invoke_synora_tool(
        "parse_schedule_draft",
        {
            "text_content": text_content,
            "attachment_ids": attachment_ids,
            "context": tool_context(context, user_id=user_id),
        },
    )
    raise_if_stream_cancelled(db, stream_id, force_database_check=True)
    finish_tool_audit(
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
    source_history = normalize_source_history(draft.source_text)

    if missing_fields:
        final_text = "我已经整理出一条日程草稿，但还缺少关键信息。请先补充后再确认。"
        mark_action_group_status(db, supersede_action_group_id, lifecycle_status="superseded", is_actionable=False)
        upsert_pending_state(
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
        card = append_message(
            db,
            thread,
            role="assistant",
            message_type="schedule_draft_card",
            status="completed",
            text_content=None,
            action_group_id=action_group_id,
            revision=revision,
            structured_payload=schedule_card_payload(
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

    conflict_audit = start_tool_audit(
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
    raise_if_stream_cancelled(db, stream_id, force_database_check=True)
    conflict_message, conflict_result = await invoke_synora_tool(
        "detect_schedule_conflicts",
        {
            "draft": draft.model_dump(mode="json", by_alias=True),
            "draft_hash": draft_hash,
            "context": tool_context(context, user_id=user_id, approval_scope=schedule_scope),
        },
    )
    raise_if_stream_cancelled(db, stream_id, force_database_check=True)
    if conflict_result.get("status") == "error":
        raise ValueError(str(conflict_result.get("message") or "日程冲突检查失败。"))
    finish_tool_audit(
        db,
        conflict_audit,
        status="ok",
        response_json={"content": str(conflict_message.content), "structured": conflict_result},
    )
    tool_events.append({"event": "tool_call_completed", "data": {"tool_name": "detect_schedule_conflicts"}})

    approval = dict(conflict_result.get("approval") or {})
    final_text = "我已经整理好日程草稿，并完成冲突检查。确认后我会正式创建日程和提醒。"
    mark_action_group_status(db, supersede_action_group_id, lifecycle_status="superseded", is_actionable=False)
    upsert_pending_state(
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
    draft_card = append_message(
        db,
        thread,
        role="assistant",
        message_type="schedule_draft_card",
        status="completed",
        text_content=None,
        action_group_id=action_group_id,
        revision=revision,
        structured_payload=schedule_card_payload(
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
    conflict_card = append_message(
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


async def process_quick_note_intake(
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
    stream_id: str | None = None,
) -> tuple[str, list[int], dict[str, Any] | None, list[dict[str, Any]]]:
    tool_events: list[dict[str, Any]] = []
    supersede_action_group_id = context.get("supersede_action_group_id")
    action_group_id = action_group_id or mint_token()
    quick_note_scope = quick_note_scope_from_action_group(action_group_id)
    tool_audit = start_tool_audit(
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
    raise_if_stream_cancelled(db, stream_id, force_database_check=True)
    tool_message, parsed = await invoke_synora_tool(
        "prepare_quick_note_draft",
        {
            "content": text_content,
            "tags": [],
            "attachment_ids": attachment_ids,
            "context": tool_context(context, user_id=user_id, approval_scope=quick_note_scope),
        },
    )
    raise_if_stream_cancelled(db, stream_id, force_database_check=True)
    finish_tool_audit(
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
    mark_action_group_status(db, supersede_action_group_id, lifecycle_status="superseded", is_actionable=False)
    upsert_pending_state(
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
    card = append_message(
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
