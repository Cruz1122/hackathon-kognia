from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    Boolean,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentSnapshot(Base):
    __tablename__ = 'agent_snapshots'
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (ForeignKeyConstraint(['conversation_id', 'organization_id'], ['conversations.id', 'conversations.organization_id'], ondelete='CASCADE'),)


class AgentOperation(Base):
    """Durable effect intent/result; also the demo booking ledger."""
    __tablename__ = 'agent_operations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    tool: Mapped[str] = mapped_column(String(80), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    __table_args__ = (ForeignKeyConstraint(['conversation_id', 'organization_id'], ['conversations.id', 'conversations.organization_id'], ondelete='CASCADE'),)


class ChannelBinding(Base):
    """Operator-verified identity and routing; never inferred from caller ID."""
    __tablename__ = 'channel_bindings'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    phone_number_id: Mapped[str] = mapped_column(String(80), nullable=False)
    wa_id: Mapped[str] = mapped_column(String(32), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    template_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    template_language: Mapped[str] = mapped_column(String(16), nullable=False, default='es')
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(['conversation_id', 'organization_id'], ['conversations.id', 'conversations.organization_id'], ondelete='CASCADE'),
        UniqueConstraint('phone_number_id', 'wa_id', name='uq_channel_identity'),
        UniqueConstraint('organization_id', 'phone', name='uq_channel_phone'),
        UniqueConstraint('conversation_id', name='uq_channel_conversation'),
    )


class ChannelEvent(Base):
    """Durable inbox/outbox. Redis is a wake-up, not the source of truth."""
    __tablename__ = 'channel_events'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('channel_bindings.id', ondelete='CASCADE'), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='pending')
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    __table_args__ = (Index('ix_channel_pending', 'status', 'created_at'),)


class UserRole(StrEnum):
    SUPERADMIN = "SUPERADMIN"
    ADMIN = "ADMIN"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class CallStatus(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class OpportunityStatus(StrEnum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"


class RecoveryChannel(StrEnum):
    WHATSAPP = "whatsapp"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    analytics_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )

    users: Mapped[list[User]] = relationship(
        back_populates="organization", lazy="noload"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="organization", lazy="noload"
    )
    customers: Mapped[list[Customer]] = relationship(back_populates="organization", lazy="noload")
    calls: Mapped[list[Call]] = relationship(back_populates="organization", lazy="noload")
    products: Mapped[list[Product]] = relationship(back_populates="organization", lazy="noload")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="organization", lazy="noload")
    objections: Mapped[list[Objection]] = relationship(back_populates="organization", lazy="noload")
    product_interests: Mapped[list[ProductInterest]] = relationship(back_populates="organization", lazy="noload")

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
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
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
    customer: Mapped[Customer | None] = relationship(back_populates="conversations", lazy="selectin", overlaps="organization,conversations")
    calls: Mapped[list[Call]] = relationship(back_populates="conversation", lazy="noload", overlaps="organization,calls")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="conversation", lazy="noload", overlaps="organization,opportunities")
    objections: Mapped[list[Objection]] = relationship(back_populates="conversation", lazy="noload", overlaps="organization,objections")
    product_interests: Mapped[list[ProductInterest]] = relationship(back_populates="conversation", lazy="noload", overlaps="organization,product_interests")

    __table_args__ = (
        CheckConstraint("length(trim(channel)) > 0", name="ck_conversations_channel_not_blank"),
        CheckConstraint("length(trim(status)) > 0", name="ck_conversations_status_not_blank"),
        Index(
            "ix_conversations_organization_created_at",
            "organization_id",
            "created_at",
        ),
        UniqueConstraint("id", "organization_id", name="uq_conversations_id_organization_id"),
        ForeignKeyConstraint(
            ["customer_id", "organization_id"],
            ["customers.id", "customers.organization_id"],
            ondelete="RESTRICT",
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
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="voice", server_default="voice")
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
        CheckConstraint("channel IN ('voice', 'whatsapp', 'system')", name="ck_messages_channel"),
        Index("ix_messages_conversation_created_at", "conversation_id", "created_at"),
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=func.now(), server_default=func.now(), nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="customers", lazy="selectin")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="customer", lazy="noload", overlaps="organization,conversations")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="customer", lazy="noload", overlaps="organization,opportunities")

    __table_args__ = (
        Index("ix_customers_organization_id", "organization_id"),
        Index("ix_customers_organization_phone", "organization_id", "phone"),
        UniqueConstraint("id", "organization_id", name="uq_customers_id_organization_id"),
    )


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[CallStatus] = mapped_column(String(16), nullable=False, default=CallStatus.ACTIVE, server_default="active")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telnyx_call_control_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    call_leg_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    call_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    recording_offset_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="calls", lazy="selectin", overlaps="calls")
    conversation: Mapped[Conversation] = relationship(back_populates="calls", lazy="noload", overlaps="organization,calls")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'ended', 'failed')", name="ck_calls_status"),
        CheckConstraint("ended_at IS NULL OR status IN ('ended', 'failed')", name="ck_calls_ended_at_status"),
        ForeignKeyConstraint(
            ["conversation_id", "organization_id"],
            ["conversations.id", "conversations.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_calls_organization_started_at", "organization_id", "started_at"),
        Index("ix_calls_conversation_id", "conversation_id"),
        Index("ix_calls_telnyx_call_control_id", "telnyx_call_control_id"),
    )


class CallEvent(Base):
    __tablename__ = "call_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    offset_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("call_id", "seq", name="uq_call_events_call_seq"),
        CheckConstraint("seq > 0", name="ck_call_events_seq_positive"),
        CheckConstraint("offset_ms >= 0", name="ck_call_events_offset_non_negative"),
        Index("ix_call_events_organization_call", "organization_id", "call_id"),
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    offset_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    is_final: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("speaker IN ('customer', 'agent')", name="ck_transcript_segments_speaker"),
        Index("ix_transcript_segments_call_offset", "call_id", "offset_ms"),
    )


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RECORDING", server_default="RECORDING")
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    waveform: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    download_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('RECORDING', 'PROCESSING', 'DOWNLOADING', 'READY', 'ERROR')",
            name="ck_recordings_status",
        ),
        Index("ix_recordings_call_id", "call_id"),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="products", lazy="selectin")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="product", lazy="noload", overlaps="organization,opportunities")
    interests: Mapped[list[ProductInterest]] = relationship(back_populates="product", lazy="noload", overlaps="organization,product_interests")

    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_products_name_not_blank"),
        UniqueConstraint("organization_id", "name", name="uq_products_organization_name"),
        UniqueConstraint("id", "organization_id", name="uq_products_id_organization_id"),
    )


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[OpportunityStatus] = mapped_column(String(16), nullable=False, default=OpportunityStatus.PENDING, server_default="pending")
    amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="COP", server_default="COP")
    lost_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_channel: Mapped[RecoveryChannel | None] = mapped_column(String(16), nullable=True)
    won_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=func.now(), server_default=func.now(), nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="opportunities", lazy="selectin", overlaps="opportunities")
    conversation: Mapped[Conversation] = relationship(back_populates="opportunities", lazy="noload", overlaps="organization,opportunities")
    customer: Mapped[Customer | None] = relationship(back_populates="opportunities", lazy="noload", overlaps="organization,opportunities,conversation")
    product: Mapped[Product | None] = relationship(back_populates="opportunities", lazy="noload", overlaps="organization,opportunities,conversation,customer")

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'won', 'lost')", name="ck_opportunities_status"),
        CheckConstraint("amount_minor IS NULL OR amount_minor >= 0", name="ck_opportunities_amount_nonnegative"),
        CheckConstraint(
            "(status = 'won' AND won_at IS NOT NULL) OR (status <> 'won' AND won_at IS NULL)",
            name="ck_opportunities_won_timestamp_consistent",
        ),
        CheckConstraint(
            "(status = 'lost' AND lost_at IS NOT NULL) OR (status <> 'lost' AND lost_at IS NULL)",
            name="ck_opportunities_lost_timestamp_consistent",
        ),
        CheckConstraint(
            "recovered_at IS NULL OR (recovery_started_at IS NOT NULL AND recovered_at >= recovery_started_at)",
            name="ck_opportunities_recovery_order",
        ),
        CheckConstraint("recovery_channel IS NULL OR recovery_channel = 'whatsapp'", name="ck_opportunities_recovery_channel"),
        ForeignKeyConstraint(["conversation_id", "organization_id"], ["conversations.id", "conversations.organization_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["customer_id", "organization_id"], ["customers.id", "customers.organization_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["product_id", "organization_id"], ["products.id", "products.organization_id"], ondelete="RESTRICT"),
        Index("ix_opportunities_organization_created_at", "organization_id", "created_at"),
        Index("ix_opportunities_organization_status_created_at", "organization_id", "status", "created_at"),
        Index("ix_opportunities_organization_recovery_started_at", "organization_id", "recovery_started_at"),
        Index("ix_opportunities_conversation_id", "conversation_id"),
        Index("ix_opportunities_product_id", "product_id"),
    )


class Objection(Base):
    __tablename__ = "objections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="objections", lazy="selectin", overlaps="objections")
    conversation: Mapped[Conversation] = relationship(back_populates="objections", lazy="noload", overlaps="organization,objections")

    __table_args__ = (
        CheckConstraint("length(trim(category)) > 0", name="ck_objections_category_not_blank"),
        CheckConstraint("source IN ('manual', 'enrichment')", name="ck_objections_source"),
        UniqueConstraint("conversation_id", "category", "source", name="uq_objections_conversation_category_source"),
        ForeignKeyConstraint(["conversation_id", "organization_id"], ["conversations.id", "conversations.organization_id"], ondelete="CASCADE"),
        Index("ix_objections_organization_created_at", "organization_id", "created_at"),
        Index("ix_objections_organization_category", "organization_id", "category"),
        Index("ix_objections_conversation_id", "conversation_id"),
    )


class ProductInterest(Base):
    __tablename__ = "product_interests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="product_interests", lazy="selectin", overlaps="product_interests,interests")
    conversation: Mapped[Conversation] = relationship(back_populates="product_interests", lazy="noload", overlaps="organization,product_interests,interests")
    product: Mapped[Product] = relationship(back_populates="interests", lazy="noload", overlaps="organization,product_interests,conversation")

    __table_args__ = (
        ForeignKeyConstraint(["conversation_id", "organization_id"], ["conversations.id", "conversations.organization_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["product_id", "organization_id"], ["products.id", "products.organization_id"], ondelete="CASCADE"),
        UniqueConstraint("conversation_id", "product_id", name="uq_product_interests_conversation_product"),
        Index("ix_product_interests_organization_created_at", "organization_id", "created_at"),
        Index("ix_product_interests_product_id", "product_id"),
    )


__all__ = [
    "Call",
    "CallStatus",
    "Conversation",
    "Customer",
    "Message",
    "MessageRole",
    "Organization",
    "Objection",
    "Opportunity",
    "OpportunityStatus",
    "Product",
    "ProductInterest",
    "RecoveryChannel",
    "User",
    "UserRole",
]
