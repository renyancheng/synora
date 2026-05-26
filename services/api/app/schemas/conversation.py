from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import SelectedTool


class ConversationThreadItem(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime


class ConversationMessageItem(BaseModel):
    id: int
    role: str
    message_type: str
    status: str = "completed"
    text_content: str | None = None
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    action_group_id: str | None = None
    revision: int = 1
    created_at: datetime


class ConversationCreateRequest(BaseModel):
    title: str | None = None


class ConversationCreateResponse(BaseModel):
    status: str = "ok"
    conversation: ConversationThreadItem


class ConversationListResponse(BaseModel):
    status: str = "ok"
    items: list[ConversationThreadItem]


class ConversationMessagesResponse(BaseModel):
    status: str = "ok"
    conversation: ConversationThreadItem
    items: list[ConversationMessageItem]


class ConversationUpdateRequest(BaseModel):
    title: str


class ConversationUpdateResponse(BaseModel):
    status: str = "ok"
    conversation: ConversationThreadItem


class ConversationSendMessageRequest(BaseModel):
    text_content: str | None = None
    attachment_ids: list[int] = Field(default_factory=list)
    selected_tool: SelectedTool | None = None
    context: dict[str, str] = Field(default_factory=dict)


class ConversationSendMessageResponse(BaseModel):
    status: str = "accepted"
    conversation: ConversationThreadItem
    user_message: ConversationMessageItem
    assistant_message_id: int
    stream_id: str


class ConversationActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ConversationActionResponse(BaseModel):
    status: str = "ok"
    conversation: ConversationThreadItem
    assistant_messages: list[ConversationMessageItem]


class ConversationDeleteResponse(BaseModel):
    status: str = "ok"
    deleted_conversation_id: int


class ConversationRewindResponse(BaseModel):
    status: str = "ok"
    conversation: ConversationThreadItem
    restored_message: ConversationMessageItem
