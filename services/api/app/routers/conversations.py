from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.domains.conversation.service import (
    apply_action,
    create_conversation,
    list_conversations,
    list_messages,
    send_message,
)
from app.models import ConversationMessage, ConversationThread, User
from app.schemas.conversation import (
    ConversationActionRequest,
    ConversationActionResponse,
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationListResponse,
    ConversationMessageItem,
    ConversationMessagesResponse,
    ConversationSendMessageRequest,
    ConversationSendMessageResponse,
    ConversationThreadItem,
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
        text_content=message.text_content,
        structured_payload=dict(message.structured_payload_json or {}),
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


@router.post("/{conversation_id}/messages", response_model=ConversationSendMessageResponse)
def send_conversation_message(
    conversation_id: int,
    payload: ConversationSendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationSendMessageResponse:
    try:
        thread, user_message, assistant_messages = send_message(db, current_user.id, conversation_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ConversationSendMessageResponse(
        conversation=_thread_item(thread),
        user_message=_message_item(user_message),
        assistant_messages=[_message_item(item) for item in assistant_messages],
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
