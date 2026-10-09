"""Create tenant-scoped organizations, users, conversations, and messages."""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0002_persistent_conversations"
down_revision: str | None = "0001_initial"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_organizations_name_not_blank"),
        sa.CheckConstraint("length(trim(slug)) > 0", name="ck_organizations_slug_not_blank"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("role IN ('SUPERADMIN', 'ADMIN')", name="ck_users_role"),
        sa.CheckConstraint(
            "(role = 'SUPERADMIN' AND organization_id IS NULL) OR "
            "(role = 'ADMIN' AND organization_id IS NOT NULL)",
            name="ck_users_organization_for_role",
        ),
        sa.CheckConstraint("length(trim(email)) > 0", name="ck_users_email_not_blank"),
        sa.CheckConstraint(
            "length(trim(password_hash)) > 0", name="ck_users_password_hash_not_blank"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("length(trim(channel)) > 0", name="ck_conversations_channel_not_blank"),
        sa.CheckConstraint("length(trim(status)) > 0", name="ck_conversations_status_not_blank"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_organization_created_at",
        "conversations",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')", name="ck_messages_role"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_messages_conversation_created_at", "messages", ["conversation_id", "created_at"]
    )

    op.execute(
        """
        CREATE FUNCTION validate_conversation_creator_tenant() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM users AS u
                WHERE u.id = NEW.created_by
                  AND (u.role = 'SUPERADMIN' OR u.organization_id = NEW.organization_id)
            ) THEN
                RAISE EXCEPTION 'Conversation creator must belong to the conversation organization';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_conversations_creator_tenant
        BEFORE INSERT OR UPDATE OF organization_id, created_by ON conversations
        FOR EACH ROW
        EXECUTE FUNCTION validate_conversation_creator_tenant()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_conversations_creator_tenant ON conversations"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_conversation_creator_tenant()")
    op.drop_index("ix_messages_conversation_created_at", table_name="messages")
    op.drop_table("messages")
    op.drop_index(
        "ix_conversations_organization_created_at", table_name="conversations"
    )
    op.drop_table("conversations")
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
