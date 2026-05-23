from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.attachment.service import build_attachment_prompt_assets
from app.domains.quick_note.service import create_quick_note_draft, save_note_after_approval
from app.domains.schedule.service import (
    build_draft_hash,
    create_schedule_after_approval,
    create_schedule_draft,
    detect_conflicts,
)
from app.models import AgentRun, ConversationMessage, ConversationPendingState, ConversationThread
from app.runtime.model_adapter import ModelAdapter
from app.schemas.conversation import ConversationActionRequest, ConversationSendMessageRequest
from app.schemas.quick_note import QuickNoteDraftRequest
from app.schemas.schedule import ScheduleDraftInput, ScheduleEventDraft
from app.security import mint_token


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
        structured_payload={},
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
    )
    db.add(agent_run)
    db.commit()
    db.refresh(agent_run)
    return thread, user_message, assistant_message, agent_run


def consume_stream(
    db: Session,
    user_id: int,
    conversation_id: int,
    stream_id: str,
) -> Generator[dict, None, None]:
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
        yield from _replay_completed_run(db, agent_run, assistant_message)
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

    yield {"event": "assistant_started", "data": {"assistant_message_id": assistant_message.id}}

    try:
        pending = _get_pending_state(db, conversation_id)
        if pending:
            final_text = "当前还有一项待确认内容。你可以先处理卡片，或者先取消当前待办后再继续新的话题。"
            yield from _emit_text_stream(db, assistant_message, final_text)
            _finalize_run(db, agent_run, assistant_message, assistant_text=final_text, created_message_ids=[])
            yield {"event": "assistant_message", "data": {"message": _message_payload(assistant_message)}}
            yield {"event": "run_completed", "data": {"stream_id": stream_id}}
            return

        intent = ModelAdapter().route_conversation_intent(
            {
                "text_content": text_content,
                "attachment_ids": attachment_ids,
                "selected_tool": selected_tool,
                "context": context,
            },
            attachment_parts=attachment_parts,
        )
        agent_run.workflow = intent
        db.commit()

        if intent == "general_chat":
            recent_messages = db.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == thread.id)
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
                .limit(6)
            ).all()
            ordered = list(reversed(list(recent_messages)))
            final_text = ""
            for chunk in ModelAdapter().stream_chat_reply_chunks(
                user_message=text_content,
                recent_messages=[
                    {"role": item.role, "content": item.text_content or ""}
                    for item in ordered
                    if item.text_content and item.id != assistant_message.id
                ],
                attachment_parts=attachment_parts,
            ):
                final_text += chunk
                assistant_message.text_content = final_text
                db.commit()
                yield {"event": "assistant_delta", "data": {"assistant_message_id": assistant_message.id, "delta": chunk}}
            assistant_message.text_content = final_text
            assistant_message.status = "completed"
            db.commit()
            _finalize_run(db, agent_run, assistant_message, assistant_text=final_text, created_message_ids=[])
            yield {"event": "assistant_message", "data": {"message": _message_payload(assistant_message)}}
            yield {"event": "run_completed", "data": {"stream_id": stream_id}}
            return

        if intent == "schedule_intake":
            yield {"event": "tool_status", "data": {"label": "正在整理日程草稿"}}
            final_text, created_ids = _process_schedule_intake(
                db,
                user_id,
                thread,
                text_content=text_content,
                attachment_ids=attachment_ids,
                context=context,
            )
        else:
            yield {"event": "tool_status", "data": {"label": "正在整理速记"}}
            final_text, created_ids = _process_quick_note_intake(
                db,
                user_id,
                thread,
                text_content=text_content,
                attachment_ids=attachment_ids,
                context=context,
            )

        yield from _emit_text_stream(db, assistant_message, final_text)
        _finalize_run(db, agent_run, assistant_message, assistant_text=final_text, created_message_ids=created_ids)
        yield {"event": "assistant_message", "data": {"message": _message_payload(assistant_message)}}
        for message_id in created_ids:
            message = db.get(ConversationMessage, message_id)
            if message:
                yield {"event": "card_upsert", "data": {"message": _message_payload(message)}}
        yield {"event": "run_completed", "data": {"stream_id": stream_id}}
    except Exception as exc:
        agent_run.status = "failed"
        agent_run.stream_status = "failed"
        agent_run.error_message = str(exc)
        agent_run.completed_at = datetime.now(timezone.utc)
        assistant_message.status = "failed"
        if not assistant_message.text_content:
            assistant_message.text_content = "这次处理没有完成，请稍后再试。"
        db.commit()
        yield {"event": "run_failed", "data": {"message": str(exc), "assistant_message_id": assistant_message.id}}


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


def _process_schedule_intake(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    *,
    text_content: str,
    attachment_ids: list[int],
    context: dict[str, str],
) -> tuple[str, list[int]]:
    draft, draft_hash, missing_fields, ambiguity_flags, evidence_digest, parse_confidence = create_schedule_draft(
        db,
        user_id,
        ScheduleDraftInput(
            text_content=text_content,
            attachment_ids=attachment_ids,
            context=context,
        ),
    )

    action_group_id = mint_token()
    revision = 1
    created_ids: list[int] = []

    if missing_fields:
        final_text = "我已经整理出一条日程草稿，不过还有必要信息缺失。请先补充后再确认。"
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
        return final_text, created_ids

    conflict_result = detect_conflicts(db, user_id, draft, draft_hash)
    final_text = "我已经帮你整理好日程草稿，并完成了冲突检查。确认后我就会正式创建日程和提醒。"
    _upsert_pending_state(
        db,
        thread.id,
        user_id,
        pending_type="schedule",
        stage="approval_pending",
        draft_hash=conflict_result.approval.draft_hash,
        approval_token=conflict_result.approval.approval_token,
        attachment_ids=draft.source_attachment_ids,
        payload_json=draft.model_dump(mode="json", by_alias=True),
        meta_json={
            "conflict_items": [item.model_dump(mode="json", by_alias=True) for item in conflict_result.conflict_items],
            "suggestions": [item.model_dump(mode="json", by_alias=True) for item in conflict_result.suggestions],
            "risk_level": conflict_result.risk_level,
            "evidence_digest": evidence_digest,
            "ambiguity_flags": ambiguity_flags,
            "parse_confidence": parse_confidence,
            "action_group_id": action_group_id,
            "revision": revision,
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
            "risk_level": conflict_result.risk_level,
            "conflict_items": [item.model_dump(mode="json", by_alias=True) for item in conflict_result.conflict_items],
            "suggestions": [item.model_dump(mode="json", by_alias=True) for item in conflict_result.suggestions],
            "action_group_id": action_group_id,
            "revision": revision,
            "lifecycle_status": "conflict_review",
            "is_actionable": False,
        },
    )
    created_ids.extend([draft_card.id, conflict_card.id])
    return final_text, created_ids


def _process_quick_note_intake(
    db: Session,
    user_id: int,
    thread: ConversationThread,
    *,
    text_content: str,
    attachment_ids: list[int],
    context: dict[str, str],
) -> tuple[str, list[int]]:
    normalized_content, preview_tags, approval_token, evidence_digest, approval = create_quick_note_draft(
        db,
        user_id,
        QuickNoteDraftRequest(
            content=text_content,
            tags=[],
            attachment_ids=attachment_ids,
            context=context,
        ),
    )
    final_text = "我已经帮你整理好这条速记，确认后就会正式保存。"
    action_group_id = mint_token()
    revision = 1
    _upsert_pending_state(
        db,
        thread.id,
        user_id,
        pending_type="quick_note",
        stage="approval_pending",
        draft_hash=approval.draft_hash,
        approval_token=approval_token,
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
    return final_text, [card.id]


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

    conflict_result = detect_conflicts(db, user_id, updated_draft, build_draft_hash(updated_draft))
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
            "start": {"dateTime": schedule.start_at.astimezone(ZoneInfo(schedule.time_zone)).isoformat(), "timeZone": schedule.time_zone},
            "end": {"dateTime": schedule.end_at.astimezone(ZoneInfo(schedule.time_zone)).isoformat(), "timeZone": schedule.time_zone},
            "channels": [job.channel for job in jobs],
        },
    )
    return [message]


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
    return [message]


def _emit_text_stream(db: Session, assistant_message: ConversationMessage, text: str) -> Iterable[dict]:
    aggregated = ""
    for index in range(0, len(text), 12):
        chunk = text[index : index + 12]
        aggregated += chunk
        assistant_message.text_content = aggregated
        db.commit()
        yield {"event": "assistant_delta", "data": {"assistant_message_id": assistant_message.id, "delta": chunk}}
    assistant_message.status = "completed"
    db.commit()


def _replay_completed_run(db: Session, agent_run: AgentRun, assistant_message: ConversationMessage) -> Generator[dict, None, None]:
    assistant_text = str(agent_run.output_json.get("assistant_text") or assistant_message.text_content or "")
    yield {"event": "assistant_started", "data": {"assistant_message_id": assistant_message.id}}
    if assistant_text:
        yield {"event": "assistant_message", "data": {"message": _message_payload(assistant_message)}}
    for message_id in list(agent_run.output_json.get("created_message_ids") or []):
        message = db.get(ConversationMessage, message_id)
        if message:
            yield {"event": "card_upsert", "data": {"message": _message_payload(message)}}
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
        "assistant_text": assistant_text,
        "created_message_ids": created_message_ids,
    }
    agent_run.completed_at = datetime.now(timezone.utc)
    db.commit()


def _mark_action_group_status(
    db: Session,
    action_group_id: str | None,
    *,
    lifecycle_status: str,
    is_actionable: bool,
) -> None:
    if not action_group_id:
        return
    messages = db.scalars(
        select(ConversationMessage).where(ConversationMessage.action_group_id == action_group_id)
    ).all()
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
            source_type="attachment",
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
        pending.source_type = "attachment"
        pending.attachment_ids_json = attachment_ids
        pending.payload_json = payload_json
        pending.meta_json = meta_json
    db.commit()
    db.refresh(pending)
    return pending


def _clear_pending_state(db: Session, pending: ConversationPendingState) -> None:
    db.delete(pending)
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
