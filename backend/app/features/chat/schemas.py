from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...db.models import MessageRole


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=10_000)


class AskRequest(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=10_000,
        description="Pregunta que se enviará al modelo seleccionado.",
        examples=["Explica qué es el streaming de tokens."],
    )
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description="Conversación persistida que aporta el historial autoritativo.",
    )
    messages: list[Message] = Field(default_factory=list, max_length=40)
    channel: str = Field(default="chat", pattern="^[a-z0-9-]{1,40}$")
    turn_id: str | None = Field(default=None, max_length=100)


class ConversationCreate(BaseModel):
    channel: str = Field(default="chat", pattern="^[a-z0-9-]{1,32}$")
    status: str = Field(default="open", pattern="^[a-z0-9-]{1,32}$")


class ConversationMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    channel: str
    created_at: datetime

    @field_validator("channel", mode="before")
    @classmethod
    def default_legacy_channel(cls, value: str | None) -> str:
        return value or "voice"


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    created_by: uuid.UUID
    customer_id: uuid.UUID | None = None
    channel: str
    status: str
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessageResponse] = Field(default_factory=list)
