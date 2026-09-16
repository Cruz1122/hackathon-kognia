from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UserRole(StrEnum):
    SUPERADMIN = "SUPERADMIN"
    ADMIN = "ADMIN"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )

    users: Mapped[list[User]] = relationship(
        back_populates="organization", lazy="noload"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="organization", lazy="noload"
    )

    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_organizations_name_not_blank"),
        CheckConstraint("length(trim(slug)) > 0", name="ck_organizations_slug_not_blank"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=16,
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )

    organization: Mapped[Organization | None] = relationship(
        back_populates="users", lazy="selectin"
    )
    created_conversations: Mapped[list[Conversation]] = relationship(
        back_populates="creator", lazy="noload"
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('SUPERADMIN', 'ADMIN')", name="ck_users_role"
        ),
        CheckConstraint(
            "(role = 'SUPERADMIN' AND organization_id IS NULL) OR "
            "(role = 'ADMIN' AND organization_id IS NOT NULL)",
            name="ck_users_organization_for_role",
        ),
        CheckConstraint("length(trim(email)) > 0", name="ck_users_email_not_blank"),
        CheckConstraint(
            "length(trim(password_hash)) > 0", name="ck_users_password_hash_not_blank"
        ),
        Index("ix_users_organization_id", "organization_id"),
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    organization: Mapped[Organization] = relationship(
        back_populates="conversations", lazy="selectin"
    )
    creator: Mapped[User] = relationship(
        back_populates="created_conversations", lazy="selectin"
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="Message.created_at",
    )

    __table_args__ = (
        CheckConstraint("length(trim(channel)) > 0", name="ck_conversations_channel_not_blank"),
        CheckConstraint("length(trim(status)) > 0", name="ck_conversations_status_not_blank"),
        Index(
            "ix_conversations_organization_created_at",
            "organization_id",
            "created_at",
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(
            MessageRole,
            name="message_role",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            length=16,
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(
        back_populates="messages", lazy="noload"
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')", name="ck_messages_role"
        ),
        Index("ix_messages_conversation_created_at", "conversation_id", "created_at"),
    )


__all__ = [
    "Conversation",
    "Message",
    "MessageRole",
    "Organization",
    "User",
    "UserRole",
]
