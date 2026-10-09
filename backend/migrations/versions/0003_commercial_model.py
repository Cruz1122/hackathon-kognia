"""Add the tenant-scoped commercial model used by analytics."""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003_commercial_model"
down_revision: str | None = "0002_persistent_conversations"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _tenant_fk(columns: list[str], target: list[str], *, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(columns, target, ondelete=ondelete)


def upgrade() -> None:
    op.add_column("conversations", sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column(
        "messages",
        sa.Column("channel", sa.String(length=16), server_default="voice", nullable=False),
    )
    op.create_check_constraint(
        "ck_messages_channel", "messages", "channel IN ('voice', 'whatsapp', 'system')"
    )
    op.create_unique_constraint(
        "uq_conversations_id_organization_id", "conversations", ["id", "organization_id"]
    )

    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_customers_id_organization_id"),
    )
    op.create_index("ix_customers_organization_id", "customers", ["organization_id"])
    op.create_index("ix_customers_organization_phone", "customers", ["organization_id", "phone"])
    op.create_foreign_key(
        "fk_conversations_customer_tenant",
        "conversations",
        "customers",
        ["customer_id", "organization_id"],
        ["id", "organization_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_products_name_not_blank"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_products_organization_name"),
        sa.UniqueConstraint("id", "organization_id", name="uq_products_id_organization_id"),
    )

    op.create_table(
        "calls",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'ended', 'failed')", name="ck_calls_status"),
        sa.CheckConstraint("ended_at IS NULL OR status IN ('ended', 'failed')", name="ck_calls_ended_at_status"),
        _tenant_fk(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        _tenant_fk(["conversation_id", "organization_id"], ["conversations.id", "conversations.organization_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calls_organization_started_at", "calls", ["organization_id", "started_at"])
    op.create_index("ix_calls_conversation_id", "calls", ["conversation_id"])

    op.create_table(
        "opportunities",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("product_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="COP", nullable=False),
        sa.Column("lost_reason", sa.String(length=64), nullable=True),
        sa.Column("recovery_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_channel", sa.String(length=16), nullable=True),
        sa.Column("won_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lost_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'won', 'lost')", name="ck_opportunities_status"),
        sa.CheckConstraint("amount_minor IS NULL OR amount_minor >= 0", name="ck_opportunities_amount_nonnegative"),
        sa.CheckConstraint("won_at IS NULL OR status = 'won'", name="ck_opportunities_won_at_status"),
        sa.CheckConstraint("lost_at IS NULL OR status = 'lost'", name="ck_opportunities_lost_at_status"),
        sa.CheckConstraint("recovered_at IS NULL OR recovery_started_at IS NOT NULL", name="ck_opportunities_recovery_order"),
        sa.CheckConstraint("recovery_channel IS NULL OR recovery_channel = 'whatsapp'", name="ck_opportunities_recovery_channel"),
        _tenant_fk(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        _tenant_fk(["conversation_id", "organization_id"], ["conversations.id", "conversations.organization_id"], ondelete="CASCADE"),
        _tenant_fk(["customer_id", "organization_id"], ["customers.id", "customers.organization_id"], ondelete="RESTRICT"),
        _tenant_fk(["product_id", "organization_id"], ["products.id", "products.organization_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_opportunities_organization_created_at", "opportunities", ["organization_id", "created_at"])
    op.create_index("ix_opportunities_organization_status_created_at", "opportunities", ["organization_id", "status", "created_at"])
    op.create_index("ix_opportunities_organization_recovery_started_at", "opportunities", ["organization_id", "recovery_started_at"])
    op.create_index("ix_opportunities_conversation_id", "opportunities", ["conversation_id"])
    op.create_index("ix_opportunities_product_id", "opportunities", ["product_id"])

    op.create_table(
        "objections",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="manual", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(category)) > 0", name="ck_objections_category_not_blank"),
        sa.CheckConstraint("source IN ('manual', 'enrichment')", name="ck_objections_source"),
        _tenant_fk(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        _tenant_fk(["conversation_id", "organization_id"], ["conversations.id", "conversations.organization_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "category", "source", name="uq_objections_conversation_category_source"),
    )
    op.create_index("ix_objections_organization_created_at", "objections", ["organization_id", "created_at"])
    op.create_index("ix_objections_organization_category", "objections", ["organization_id", "category"])
    op.create_index("ix_objections_conversation_id", "objections", ["conversation_id"])

    op.create_table(
        "product_interests",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        _tenant_fk(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        _tenant_fk(["conversation_id", "organization_id"], ["conversations.id", "conversations.organization_id"], ondelete="CASCADE"),
        _tenant_fk(["product_id", "organization_id"], ["products.id", "products.organization_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "product_id", name="uq_product_interests_conversation_product"),
    )
    op.create_index("ix_product_interests_organization_created_at", "product_interests", ["organization_id", "created_at"])
    op.create_index("ix_product_interests_product_id", "product_interests", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_product_interests_product_id", table_name="product_interests")
    op.drop_index("ix_product_interests_organization_created_at", table_name="product_interests")
    op.drop_table("product_interests")
    op.drop_index("ix_objections_conversation_id", table_name="objections")
    op.drop_index("ix_objections_organization_category", table_name="objections")
    op.drop_index("ix_objections_organization_created_at", table_name="objections")
    op.drop_table("objections")
    op.drop_index("ix_opportunities_product_id", table_name="opportunities")
    op.drop_index("ix_opportunities_conversation_id", table_name="opportunities")
    op.drop_index("ix_opportunities_organization_recovery_started_at", table_name="opportunities")
    op.drop_index("ix_opportunities_organization_status_created_at", table_name="opportunities")
    op.drop_index("ix_opportunities_organization_created_at", table_name="opportunities")
    op.drop_table("opportunities")
    op.drop_index("ix_calls_conversation_id", table_name="calls")
    op.drop_index("ix_calls_organization_started_at", table_name="calls")
    op.drop_table("calls")
    op.drop_table("products")
    op.drop_constraint("fk_conversations_customer_tenant", "conversations", type_="foreignkey")
    op.drop_index("ix_customers_organization_phone", table_name="customers")
    op.drop_index("ix_customers_organization_id", table_name="customers")
    op.drop_table("customers")
    op.drop_constraint("uq_conversations_id_organization_id", "conversations", type_="unique")
    op.drop_constraint("ck_messages_channel", "messages", type_="check")
    op.drop_column("messages", "channel")
    op.drop_column("conversations", "customer_id")
