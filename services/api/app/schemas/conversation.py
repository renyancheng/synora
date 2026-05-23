from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import SourceType


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
    text_content: str | None = None
    structured_payload: dict[str, Any] = Field(default_factory=dict)
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


class ConversationSendMessageRequest(BaseModel):
    source_type: SourceType = "text"
    text_content: str | None = None
    attachment_ids: list[int] = Field(default_factory=list)
    context: dict[str, str] = Field(default_factory=dict)


class ConversationSendMessageResponse(BaseModel):
    status: str = "ok"
    conversation: ConversationThreadItem
    user_message: ConversationMessageItem
    assistant_messages: list[ConversationMessageItem]


class ConversationActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ConversationActionResponse(BaseModel):
    status: str = "ok"
    conversation: ConversationThreadItem
    assistant_messages: list[ConversationMessageItem]
