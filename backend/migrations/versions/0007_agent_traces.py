"""Agent model traces per turn. Does not edit earlier migrations."""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0007_agent_traces"
down_revision: str | None = "0006_telnyx_call_platform"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="ok", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('ok', 'error', 'cancelled')", name="ck_agent_traces_status"),
    )
    op.create_index(
        "ix_agent_traces_organization_call",
        "agent_traces",
        ["organization_id", "call_id"],
    )
    op.create_index(
        "ix_agent_traces_organization_conversation",
        "agent_traces",
        ["organization_id", "conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_traces_organization_conversation", table_name="agent_traces")
    op.drop_index("ix_agent_traces_organization_call", table_name="agent_traces")
    op.drop_table("agent_traces")
