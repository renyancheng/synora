from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.quick_note.service import create_quick_note_draft, save_note_after_approval
from app.domains.schedule.service import (
    build_draft_hash,
    create_schedule_after_approval,
    create_schedule_draft,
    detect_conflicts,
)
from app.models import ConversationMessage, ConversationPendingState, ConversationThread
from app.runtime.model_adapter import ModelAdapter
from app.schemas.conversation import ConversationActionRequest, ConversationSendMessageRequest
from app.schemas.quick_note import QuickNoteDraftRequest
from app.schemas.schedule import ScheduleDraft, ScheduleDraftInput


DEFAULT_THREAD_TITLE = "新对话"


def list_conversations(db: Session, user_id: int) -> list[ConversationThread]:
    return db.scalars(
        select(ConversationThread)
        .where(ConversationThread.user_id == user_id)
        .order_by(ConversationThread.last_message_at.desc(), ConversationThread.id.desc())
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


def send_message(
    db: Session,
    user_id: int,
    conversation_id: int,
    payload: ConversationSendMessageRequest,
) -> tuple[ConversationThread, ConversationMessage, list[ConversationMessage]]:
    thread = get_conversation(db, user_id, conversation_id)
    existing_user_message = db.scalar(
        select(ConversationMessage.id)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "user",
        )
        .limit(1)
    )
    user_message = _append_message(
        db,
        thread,
        role="user",
        message_type="text",
        text_content=(payload.text_content or "").strip(),
        structured_payload={},
    )
    if existing_user_message is None and payload.text_content:
        thread.title = ModelAdapter().generate_conversation_title(payload.text_content)
        db.commit()
        db.refresh(thread)

    pending = _get_pending_state(db, conversation_id)
    if pending:
        assistant_message = _append_message(
            db,
            thread,
            role="assistant",
            message_type="text",
            text_content="当前还有一项待确认内容。你可以先处理卡片，或者先取消当前待办后再继续新的话题。",
            structured_payload={},
        )
        return thread, user_message, [assistant_message]

    intent = ModelAdapter().route_conversation_intent(
        {
            "source_type": payload.source_type,
            "text_content": payload.text_content,
            "attachment_ids": payload.attachment_ids,
            "context": payload.context,
        }
    )
    if intent == "schedule_intake":
        return thread, user_message, _handle_schedule_intake(db, user_id, thread, payload)
    if intent == "quick_note_intake":
        return thread, user_message, _handle_quick_note_intake(db, user_id, thread, payload)
    return thread, user_message, _handle_general_chat(db, thread, payload.text_content or "")


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
        _clear_pending_state(db, pending)
        message = _append_message(
            db,
            thread,
            role="assistant",
            message_type="result_card",
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
        if pending.pending_type != "schedule" or pending.stage != "awaiting_confirmation":
            raise ValueError("当前没有可确认的日程草稿。")
        return thread, _confirm_schedule_pending(db, user_id, thread, pending)

    if action == "confirm_quick_note":
        if pending.pending_type != "quick_note" or pending.stage != "awaiting_confirmation":
            raise ValueError("当前没有可确认的速记草稿。")
        return thread, _confirm_quick_note_pending(db, user_id, thread, pending)

    raise ValueError("不支持的对话动作。")


def _handle_general_chat(db: Session, thread: ConversationThread, text: str) -> list[ConversationMessage]:
    recent_messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == thread.id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(6)
    ).all()
    ordered = list(reversed(list(recent_messages)))
    response_text = ModelAdapter().generate_chat_reply(
        user_message=text,
        recent_messages=[
            {"role": item.role, "content": item.text_content or ""}
            for item in ordered
            if item.text_content
        ],
    )
    assistant_message = _append_message(
        db,
        thread,
        role="assistant",
        message_type="text",
        text_content=response_text,
        structured_payload={},
    )
    return [assistant_message]


def _handle_schedule_intake(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    payload: ConversationSendMessageRequest,
) -> list[ConversationMessage]:
    draft, draft_hash, missing_fields, ambiguity_flags, evidence_digest, parse_confidence = create_schedule_draft(
        db,
        user_id,
        ScheduleDraftInput(
            source_type=payload.source_type,
            text_content=payload.text_content,
            attachment_ids=payload.attachment_ids,
            context=payload.context,
        ),
    )

    assistant_messages: list[ConversationMessage] = []
    if missing_fields:
        _upsert_pending_state(
            db,
            thread.id,
            user_id,
            pending_type="schedule",
            stage="awaiting_missing_fields",
            draft_hash=draft_hash,
            approval_token=None,
            source_type=draft.source_type,
            attachment_ids=draft.source_attachment_ids,
            payload_json=draft.model_dump(mode="json"),
            meta_json={
                "missing_fields": missing_fields,
                "ambiguity_flags": ambiguity_flags,
                "evidence_digest": evidence_digest,
                "parse_confidence": parse_confidence,
            },
        )
        assistant_messages.append(
            _append_message(
                db,
                thread,
                role="assistant",
                message_type="text",
                text_content="我已经整理出一条日程草稿，不过还有必要信息缺失。请在卡片里补充后继续确认。",
                structured_payload={},
            )
        )
        assistant_messages.append(
            _append_message(
                db,
                thread,
                role="assistant",
                message_type="schedule_draft_card",
                text_content=None,
                structured_payload={
                    "card_type": "schedule_draft",
                    "draft": draft.model_dump(mode="json"),
                    "missing_fields": missing_fields,
                    "ambiguity_flags": ambiguity_flags,
                    "evidence_digest": evidence_digest,
                    "parse_confidence": parse_confidence,
                    "stage": "awaiting_missing_fields",
                    "actions": ["submit_missing_fields", "dismiss_pending_action"],
                },
            )
        )
        return assistant_messages

    conflict_result = detect_conflicts(db, user_id, draft, draft_hash)
    _upsert_pending_state(
        db,
        thread.id,
        user_id,
        pending_type="schedule",
        stage="awaiting_confirmation",
        draft_hash=conflict_result.approval.draft_hash,
        approval_token=conflict_result.approval.approval_token,
        source_type=draft.source_type,
        attachment_ids=draft.source_attachment_ids,
        payload_json=draft.model_dump(mode="json"),
        meta_json={
            "conflict_items": [item.model_dump(mode="json") for item in conflict_result.conflict_items],
            "suggestions": [item.model_dump(mode="json") for item in conflict_result.suggestions],
            "risk_level": conflict_result.risk_level,
            "evidence_digest": evidence_digest,
            "ambiguity_flags": ambiguity_flags,
            "parse_confidence": parse_confidence,
        },
    )
    assistant_messages.append(
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="text",
            text_content="我已经帮你整理好日程草稿，并完成了冲突检测。确认后我就会正式创建日程和提醒。",
            structured_payload={},
        )
    )
    assistant_messages.append(
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="schedule_draft_card",
            text_content=None,
            structured_payload={
                "card_type": "schedule_draft",
                "draft": draft.model_dump(mode="json"),
                "missing_fields": [],
                "ambiguity_flags": ambiguity_flags,
                "evidence_digest": evidence_digest,
                "parse_confidence": parse_confidence,
                "stage": "awaiting_confirmation",
                "actions": ["confirm_schedule_draft", "dismiss_pending_action"],
            },
        )
    )
    assistant_messages.append(
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="conflict_card",
            text_content=None,
            structured_payload={
                "card_type": "conflict_check",
                "risk_level": conflict_result.risk_level,
                "conflict_items": [item.model_dump(mode="json") for item in conflict_result.conflict_items],
                "suggestions": [item.model_dump(mode="json") for item in conflict_result.suggestions],
            },
        )
    )
    return assistant_messages


def _handle_quick_note_intake(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    payload: ConversationSendMessageRequest,
) -> list[ConversationMessage]:
    normalized_content, preview_tags, approval_token, evidence_digest, approval = create_quick_note_draft(
        db,
        user_id,
        QuickNoteDraftRequest(
            source_type=payload.source_type,
            content=payload.text_content,
            tags=[],
            attachment_ids=payload.attachment_ids,
            context=payload.context,
        ),
    )
    _upsert_pending_state(
        db,
        thread.id,
        user_id,
        pending_type="quick_note",
        stage="awaiting_confirmation",
        draft_hash=approval.draft_hash,
        approval_token=approval_token,
        source_type=payload.source_type,
        attachment_ids=payload.attachment_ids,
        payload_json={
            "content": normalized_content,
            "tags": preview_tags,
            "source_type": payload.source_type,
            "attachment_ids": payload.attachment_ids,
        },
        meta_json={"evidence_digest": evidence_digest},
    )
    return [
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="text",
            text_content="我已经帮你整理好这条速记，确认后就会正式保存。",
            structured_payload={},
        ),
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="quick_note_preview_card",
            text_content=None,
            structured_payload={
                "card_type": "quick_note_preview",
                "normalized_content": normalized_content,
                "preview_tags": preview_tags,
                "evidence_digest": evidence_digest,
                "actions": ["confirm_quick_note", "dismiss_pending_action"],
            },
        ),
    ]


def _submit_schedule_missing_fields(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    pending: ConversationPendingState,
    payload: dict,
) -> list[ConversationMessage]:
    existing_draft = ScheduleDraft.model_validate(pending.payload_json)
    merged_payload = existing_draft.model_dump(mode="json")
    for field_name in ("title", "location", "details", "scheduled_at"):
        if payload.get(field_name):
            merged_payload[field_name] = payload[field_name]

    updated_draft = ScheduleDraft.model_validate(merged_payload)
    if updated_draft.scheduled_at:
        updated_draft = updated_draft.model_copy(
            update={"reminder_at": ModelAdapter.compute_reminder_at(updated_draft.scheduled_at)}
        )
    draft_hash = build_draft_hash(updated_draft)
    missing_fields: list[str] = []
    if not updated_draft.title.strip():
        missing_fields.append("title")
    if updated_draft.scheduled_at is None:
        missing_fields.append("scheduled_at")

    if missing_fields:
        meta_json = {
            **pending.meta_json,
            "missing_fields": missing_fields,
        }
        _upsert_pending_state(
            db,
            thread.id,
            user_id,
            pending_type="schedule",
            stage="awaiting_missing_fields",
            draft_hash=draft_hash,
            approval_token=None,
            source_type=updated_draft.source_type,
            attachment_ids=updated_draft.source_attachment_ids,
            payload_json=updated_draft.model_dump(mode="json"),
            meta_json=meta_json,
        )
        return [
            _append_message(
                db,
                thread,
                role="assistant",
                message_type="text",
                text_content="我收到了补充信息，但还差一些关键字段。请继续完善后再确认。",
                structured_payload={},
            ),
            _append_message(
                db,
                thread,
                role="assistant",
                message_type="schedule_draft_card",
                text_content=None,
                structured_payload={
                    "card_type": "schedule_draft",
                    "draft": updated_draft.model_dump(mode="json"),
                    "missing_fields": missing_fields,
                    "ambiguity_flags": list(pending.meta_json.get("ambiguity_flags", [])),
                    "evidence_digest": list(pending.meta_json.get("evidence_digest", [])),
                    "parse_confidence": float(pending.meta_json.get("parse_confidence", 0.0)),
                    "stage": "awaiting_missing_fields",
                    "actions": ["submit_missing_fields", "dismiss_pending_action"],
                },
            )
        ]

    conflict_result = detect_conflicts(db, user_id, updated_draft, draft_hash)
    _upsert_pending_state(
        db,
        thread.id,
        user_id,
        pending_type="schedule",
        stage="awaiting_confirmation",
        draft_hash=conflict_result.approval.draft_hash,
        approval_token=conflict_result.approval.approval_token,
        source_type=updated_draft.source_type,
        attachment_ids=updated_draft.source_attachment_ids,
        payload_json=updated_draft.model_dump(mode="json"),
        meta_json={
            "conflict_items": [item.model_dump(mode="json") for item in conflict_result.conflict_items],
            "suggestions": [item.model_dump(mode="json") for item in conflict_result.suggestions],
            "risk_level": conflict_result.risk_level,
            "evidence_digest": list(pending.meta_json.get("evidence_digest", [])),
            "ambiguity_flags": list(pending.meta_json.get("ambiguity_flags", [])),
            "parse_confidence": float(pending.meta_json.get("parse_confidence", 0.0)),
        },
    )
    return [
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="text",
            text_content="补充信息已收到。我重新整理了日程草稿，并完成了冲突检测，请确认。",
            structured_payload={},
        ),
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="schedule_draft_card",
            text_content=None,
            structured_payload={
                "card_type": "schedule_draft",
                "draft": updated_draft.model_dump(mode="json"),
                "missing_fields": [],
                "ambiguity_flags": list(pending.meta_json.get("ambiguity_flags", [])),
                "evidence_digest": list(pending.meta_json.get("evidence_digest", [])),
                "parse_confidence": float(pending.meta_json.get("parse_confidence", 0.0)),
                "stage": "awaiting_confirmation",
                "actions": ["confirm_schedule_draft", "dismiss_pending_action"],
            },
        ),
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="conflict_card",
            text_content=None,
            structured_payload={
                "card_type": "conflict_check",
                "risk_level": conflict_result.risk_level,
                "conflict_items": [item.model_dump(mode="json") for item in conflict_result.conflict_items],
                "suggestions": [item.model_dump(mode="json") for item in conflict_result.suggestions],
            },
        ),
    ]


def _confirm_schedule_pending(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    pending: ConversationPendingState,
) -> list[ConversationMessage]:
    draft = ScheduleDraft.model_validate(pending.payload_json)
    schedule, jobs = create_schedule_after_approval(db, user_id, pending.approval_token or "", draft)
    _clear_pending_state(db, pending)
    return [
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="result_card",
            text_content="日程已创建完成。",
            structured_payload={
                "card_type": "result",
                "result_kind": "schedule_saved",
                "schedule_id": schedule.id,
                "title": schedule.title,
                "scheduled_at": schedule.scheduled_at.isoformat(),
                "reminder_at": schedule.reminder_at.isoformat(),
                "location": schedule.location,
                "channels": [job.channel for job in jobs],
                "summary": "已创建日程并生成提醒。",
            },
        )
    ]


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
        source_type=str(payload.get("source_type") or "text"),
        attachment_ids=list(payload.get("attachment_ids") or []),
        approval_token=pending.approval_token or "",
    )
    _clear_pending_state(db, pending)
    return [
        _append_message(
            db,
            thread,
            role="assistant",
            message_type="result_card",
            text_content="速记已保存。",
            structured_payload={
                "card_type": "result",
                "result_kind": "quick_note_saved",
                "note_id": note.id,
                "content": note.content,
                "tags": list(note.topic_tags_json),
                "summary": "已保存这条速记。",
            },
        )
    ]


def _append_message(
    db: Session,
    thread: ConversationThread,
    *,
    role: str,
    message_type: str,
    text_content: str | None,
    structured_payload: dict,
) -> ConversationMessage:
    now = datetime.now(timezone.utc)
    message = ConversationMessage(
        conversation_id=thread.id,
        role=role,
        message_type=message_type,
        text_content=text_content,
        structured_payload_json=structured_payload,
        created_at=now,
    )
    thread.updated_at = now
    thread.last_message_at = now
    db.add(message)
    db.commit()
    db.refresh(message)
    db.refresh(thread)
    return message


def _get_pending_state(db: Session, conversation_id: int) -> ConversationPendingState | None:
    return db.scalar(
        select(ConversationPendingState).where(ConversationPendingState.conversation_id == conversation_id)
    )


def _upsert_pending_state(
    db: Session,
    conversation_id: int,
    user_id: int,
    *,
    pending_type: str,
    stage: str,
    draft_hash: str | None,
    approval_token: str | None,
    source_type: str,
    attachment_ids: list[int],
    payload_json: dict,
    meta_json: dict,
) -> ConversationPendingState:
    pending = _get_pending_state(db, conversation_id)
    if pending is None:
        pending = ConversationPendingState(
            conversation_id=conversation_id,
            user_id=user_id,
            pending_type=pending_type,
            stage=stage,
            draft_hash=draft_hash,
            approval_token=approval_token,
            source_type=source_type,
            attachment_ids_json=attachment_ids,
            payload_json=payload_json,
            meta_json=meta_json,
        )
        db.add(pending)
    else:
        pending.user_id = user_id
        pending.pending_type = pending_type
        pending.stage = stage
        pending.draft_hash = draft_hash
        pending.approval_token = approval_token
        pending.source_type = source_type
        pending.attachment_ids_json = attachment_ids
        pending.payload_json = payload_json
        pending.meta_json = meta_json
    db.commit()
    db.refresh(pending)
    return pending


def _clear_pending_state(db: Session, pending: ConversationPendingState) -> None:
    db.delete(pending)
    db.commit()


def hard_delete_conversation(db: Session, user_id: int, conversation_id: int) -> None:
    thread = get_conversation(db, user_id, conversation_id)
    db.delete(thread)
    db.commit()
