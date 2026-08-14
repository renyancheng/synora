"""会话待确认状态与卡片生命周期服务。

承载日程 / 速记草稿卡的公共生命周期：
- 待确认状态（ConversationPendingState）增删改查与跨天意图标记
- action_group 状态标记与终端摘要（confirmed / superseded / cancelled）
- 审批请求清理、卡片 payload 组装
- 草稿续接（无 pending 时从最近未决卡重建 context）与 pending 再生
- 补充字段、确认日程 / 速记等卡片动作落库

本模块不依赖 conversation.service，只依赖 stream_runtime、schedule / quick_note /
attachment 领域服务与基础设施，供 service、intake_service 与 agent 节点复用。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.attachment.service import build_attachment_prompt_assets
from app.domains.conversation.stream_runtime import append_message, enqueue_memory_writeback
from app.domains.quick_note.service import save_note_after_approval
from app.domains.schedule.service import build_draft_hash, create_schedule_after_approval, detect_conflicts, normalize_reminder_preset
from app.models import ApprovalRequest, Attachment, ConversationMessage, ConversationPendingState, ConversationThread
from app.schemas.schedule import ScheduleEventDraft
from app.security import mint_token, sha256_text
from app.tasks.memory import write_user_memory

logger = logging.getLogger(__name__)


def normalize_source_history(*values: Any) -> list[str]:
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


def schedule_scope_from_action_group(action_group_id: str | None) -> str | None:
    if not action_group_id:
        return None
    return f"conversation_schedule:{action_group_id}"


def quick_note_scope_from_action_group(action_group_id: str | None) -> str | None:
    if not action_group_id:
        return None
    return f"conversation_quick_note:{action_group_id}"


def schedule_draft_summary(draft: ScheduleEventDraft) -> str:
    parts = [draft.title.strip()]
    if draft.location:
        parts.append(f"地点：{draft.location.strip()}")
    if draft.details.strip():
        parts.append(f"详情：{draft.details.strip()}")
    parts.append(f"开始：{draft.start.date_time.isoformat()}")
    parts.append(f"结束：{draft.end.date_time.isoformat()}")
    return "\n".join(part for part in parts if part)


def tool_context(
    context: dict[str, Any],
    *,
    user_id: int,
    approval_scope: str | None = None,
) -> dict[str, Any]:
    tool_context_values: dict[str, Any] = {}
    for key, value in context.items():
        if value is None:
            continue
        if isinstance(value, list):
            tool_context_values[str(key)] = [str(item) for item in value if item is not None]
            continue
        if isinstance(value, dict):
            tool_context_values[str(key)] = {str(child_key): value[child_key] for child_key in value}
            continue
        tool_context_values[str(key)] = str(value)
    tool_context_values["user_id"] = str(user_id)
    if approval_scope:
        tool_context_values["approval_scope"] = approval_scope
    return tool_context_values


def get_pending_state(db: Session, conversation_id: int) -> ConversationPendingState | None:
    return db.scalar(select(ConversationPendingState).where(ConversationPendingState.conversation_id == conversation_id))


def upsert_pending_state(
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
    pending = get_pending_state(db, conversation_id)
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


def clear_pending_state(db: Session, pending: ConversationPendingState) -> None:
    db.delete(pending)
    db.commit()


def mark_cross_day_intent(db: Session, conversation_id: int, *, planned_at: datetime) -> ConversationPendingState | None:
    """将挂起会话标记为跨天意图：到 ``planned_at`` 时由 beat 任务主动唤醒跟进。

    供意图路由在识别到“改天再处理”时调用；返回更新后的 pending，若该会话无
    挂起状态则返回 None（不做任何事）。
    """
    pending = get_pending_state(db, conversation_id)
    if not pending:
        return None
    pending.intent_type = "cross_day"
    pending.planned_at = planned_at
    db.commit()
    db.refresh(pending)
    return pending


def mark_action_group_status(
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


def schedule_card_payload(
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


def delete_approvals_for_action_groups(
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
            schedule_scope_from_action_group(action_group_id),
            quick_note_scope_from_action_group(action_group_id),
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


def build_user_message_payload(
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


def resolve_contextual_draft_followup(
    db: Session,
    conversation_id: int,
    text_content: str,
    context: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """无 pending 时，从最近一张未决草稿卡重建续接 context。

    兜底路径：若首条消息未正确进入 intake（历史路由遗漏），第二条“补充/修正”
    消息借助上一条 schedule_draft_card / quick_note_preview_card 重建与
    ``prepare_pending_regeneration`` 同构的 context，驱动 parse_schedule_draft
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
        except Exception as exc:
            # 降级路径：草稿卡载荷损坏无法续接，走常规 intake 路由；结构化记录。
            logger.warning(
                "contextual_followup_degraded conversation_id=%s operation=resolve_contextual_draft_followup reason=invalid_card_payload detail=%s",
                conversation_id,
                type(exc).__name__,
            )
            return None
        previous_context["pending_regeneration"] = "schedule"
        previous_context["source_history"] = normalize_source_history(draft.source_text, text_content)
        previous_context["previous_draft_summary"] = schedule_draft_summary(draft)
        return "schedule_intake", previous_context

    previous_context["pending_regeneration"] = "quick_note"
    previous_context["previous_note_content"] = str(payload.get("normalized_content") or "").strip()
    previous_context["latest_user_text"] = text_content.strip()
    return "quick_note_intake", previous_context


def prepare_pending_regeneration(
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
        source_history = normalize_source_history(
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
        previous_context["previous_draft_summary"] = schedule_draft_summary(previous_draft)
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


def submit_schedule_missing_fields(
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
    mark_action_group_status(db, action_group_id, lifecycle_status="superseded", is_actionable=False)
    source_history = normalize_source_history(
        pending.meta_json.get("source_history"),
        updated_draft.source_text,
    )
    updated_draft = updated_draft.model_copy(update={"source_text": "\n\n".join(source_history)})

    if missing_fields:
        draft_hash = build_draft_hash(updated_draft)
        upsert_pending_state(
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
            append_message(
                db,
                thread,
                role="assistant",
                message_type="schedule_draft_card",
                status="completed",
                text_content=None,
                action_group_id=action_group_id,
                revision=revision,
                structured_payload=schedule_card_payload(
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
        approval_scope=schedule_scope_from_action_group(action_group_id),
    )
    upsert_pending_state(
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


def confirm_schedule_pending(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    pending: ConversationPendingState,
) -> list[ConversationMessage]:
    draft = ScheduleEventDraft.model_validate(pending.payload_json)
    action_group_id = pending.meta_json.get("action_group_id")
    schedule, jobs = create_schedule_after_approval(db, user_id, pending.approval_token or "", draft)
    try:
        mark_action_group_status(
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
        clear_pending_state(db, pending)
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
    enqueue_memory_writeback(
        user_id=user_id,
        source_kind="confirmed_schedule",
        source_ref_id=str(schedule.id),
        text=f"{schedule.title} {schedule.details}".strip(),
        summary="已确认日程",
        conversation_id=thread.id,
    )
    return []


def confirm_quick_note_pending(
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
    mark_action_group_status(
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
    clear_pending_state(db, pending)
    try:
        write_user_memory.delay(
            user_id=user_id,
            source_kind="confirmed_quick_note",
            source_ref_id=str(note.id),
            text=note.content,
            summary="已确认速记",
        )
    except Exception:
        # 降级路径：记忆投递失败不阻断确认结果，仅结构化记录。
        logger.warning(
            "memory_writeback_enqueue_failed conversation_id=%s operation=confirmed_quick_note reason=broker_unreachable",
            thread.id,
            exc_info=True,
        )
    return []
