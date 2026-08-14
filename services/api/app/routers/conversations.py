import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.domains.conversation.service import (
    abort_stream,
    apply_action,
    consume_stream,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    queue_message,
    resume_stream_from_checkpoint,
    rewind_last_turn,
    update_conversation_title,
)
from app.models import ConversationMessage, ConversationThread, User
from app.schemas.conversation import (
    ConversationActionRequest,
    ConversationActionResponse,
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationDeleteResponse,
    ConversationListResponse,
    ConversationMessageItem,
    ConversationMessagesResponse,
    ConversationRewindResponse,
    ConversationSendMessageRequest,
    ConversationSendMessageResponse,
    ConversationThreadItem,
    ConversationUpdateRequest,
    ConversationUpdateResponse,
)

router = APIRouter(prefix="/agent/conversations", tags=["conversations"])


def _thread_item(thread: ConversationThread) -> ConversationThreadItem:
    return ConversationThreadItem(
        id=thread.id,
        title=thread.title,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        last_message_at=thread.last_message_at,
    )


def _message_item(message: ConversationMessage) -> ConversationMessageItem:
    return ConversationMessageItem(
        id=message.id,
        role=message.role,
        message_type=message.message_type,
        status=message.status,
        text_content=message.text_content,
        structured_payload=dict(message.structured_payload_json or {}),
        action_group_id=message.action_group_id,
        revision=message.revision,
        created_at=message.created_at,
    )


@router.get("", response_model=ConversationListResponse)
def get_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationListResponse:
    items = list_conversations(db, current_user.id)
    return ConversationListResponse(items=[_thread_item(item) for item in items])


@router.post("", response_model=ConversationCreateResponse)
def create_conversation_endpoint(
    payload: ConversationCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationCreateResponse:
    thread = create_conversation(db, current_user.id, title=payload.title)
    return ConversationCreateResponse(conversation=_thread_item(thread))


@router.get("/{conversation_id}/messages", response_model=ConversationMessagesResponse)
def get_conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationMessagesResponse:
    try:
        thread, messages = list_messages(db, current_user.id, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ConversationMessagesResponse(
        conversation=_thread_item(thread),
        items=[_message_item(item) for item in messages],
    )


@router.patch("/{conversation_id}", response_model=ConversationUpdateResponse)
def rename_conversation(
    conversation_id: int,
    payload: ConversationUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationUpdateResponse:
    try:
        thread = update_conversation_title(db, current_user.id, conversation_id, payload.title)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ConversationUpdateResponse(conversation=_thread_item(thread))


@router.delete("/{conversation_id}", response_model=ConversationDeleteResponse)
def remove_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationDeleteResponse:
    try:
        delete_conversation(db, current_user.id, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ConversationDeleteResponse(deleted_conversation_id=conversation_id)


@router.post("/{conversation_id}/messages", response_model=ConversationSendMessageResponse)
def send_conversation_message(
    conversation_id: int,
    payload: ConversationSendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationSendMessageResponse:
    try:
        thread, user_message, assistant_message, agent_run = queue_message(db, current_user.id, conversation_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ConversationSendMessageResponse(
        conversation=_thread_item(thread),
        user_message=_message_item(user_message),
        assistant_message_id=assistant_message.id,
        stream_id=agent_run.stream_token or "",
    )


@router.get("/{conversation_id}/streams/{stream_id}")
async def stream_conversation_message(
    conversation_id: int,
    stream_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    try:
        get_conversation(db, current_user.id, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def event_source():
        try:
            async for item in consume_stream(db, current_user.id, conversation_id, stream_id):
                event = item["event"]
                data = json.dumps(item["data"], ensure_ascii=False)
                yield f"event: {event}\ndata: {data}\n\n"
        except ValueError as exc:
            payload = json.dumps(
                {
                    "code": "conversation_stream_error",
                    "message": str(exc),
                    "retryable": False,
                },
                ensure_ascii=False,
            )
            yield f"event: run_failed\ndata: {payload}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{conversation_id}/streams/{stream_id}/abort", status_code=status.HTTP_202_ACCEPTED)
def abort_conversation_stream(
    conversation_id: int,
    stream_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """中断一个进行中的 SSE 流（发送中点击停止）。

    持久化标记当前用户会话下的 pending/active 流为 cancelling；运行节点在
    下一检查点读取该状态后以 run_cancelled 收口，跨 worker 和重启同样有效。
    """
    try:
        get_conversation(db, current_user.id, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if not abort_stream(
        db,
        user_id=current_user.id,
        conversation_id=conversation_id,
        stream_id=stream_id,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话流不存在或已结束。")
    return {"status": "cancelling"}


@router.post("/{conversation_id}/streams/{stream_id}/resume")
async def resume_conversation_stream(
    conversation_id: int,
    stream_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """仅继续已到 finalize 前的安全 checkpoint；其它中断位置拒绝恢复。"""
    try:
        get_conversation(db, current_user.id, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def event_source():
        try:
            async for item in resume_stream_from_checkpoint(db, current_user.id, conversation_id, stream_id):
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
        except ValueError as exc:
            payload = json.dumps({"code": "conversation_stream_resume_error", "message": str(exc), "retryable": False}, ensure_ascii=False)
            yield f"event: run_failed\ndata: {payload}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/{conversation_id}/actions", response_model=ConversationActionResponse)
def perform_conversation_action(
    conversation_id: int,
    payload: ConversationActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationActionResponse:
    try:
        thread, assistant_messages = apply_action(db, current_user.id, conversation_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ConversationActionResponse(
        conversation=_thread_item(thread),
        assistant_messages=[_message_item(item) for item in assistant_messages],
    )


@router.post("/{conversation_id}/rewind-last-turn", response_model=ConversationRewindResponse)
def rewind_conversation_last_turn(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationRewindResponse:
    try:
        thread, restored_message = rewind_last_turn(db, current_user.id, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ConversationRewindResponse(
        conversation=_thread_item(thread),
        restored_message=_message_item(restored_message),
    )
