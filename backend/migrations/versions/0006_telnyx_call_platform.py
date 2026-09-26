"""Telnyx call timeline tables. Does not edit earlier migrations."""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_telnyx_call_platform"
down_revision: str | None = "0005_opp_timestamp_consistency"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("telnyx_call_control_id", sa.String(length=128), nullable=True))
    op.add_column("calls", sa.Column("call_leg_id", sa.String(length=128), nullable=True))
    op.add_column("calls", sa.Column("call_session_id", sa.String(length=128), nullable=True))
    op.add_column(
        "calls",
        sa.Column("lifecycle_state", sa.String(length=32), nullable=False, server_default="active"),
    )
    op.add_column(
        "calls",
        sa.Column("recording_offset_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "calls",
        sa.Column("next_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_calls_telnyx_call_control_id", "calls", ["telnyx_call_control_id"])
    op.create_table(
        "call_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("offset_ms", sa.Integer(), nullable=False),
        sa.Column("provider_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id", "seq", name="uq_call_events_call_seq"),
        sa.CheckConstraint("seq > 0", name="ck_call_events_seq_positive"),
        sa.CheckConstraint("offset_ms >= 0", name="ck_call_events_offset_non_negative"),
    )
    op.create_index("ix_call_events_organization_call", "call_events", ["organization_id", "call_id"])
    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("offset_ms", sa.Integer(), nullable=False),
        sa.Column("is_final", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("speaker IN ('customer', 'agent')", name="ck_transcript_segments_speaker"),
    )
    op.create_index("ix_transcript_segments_call_offset", "transcript_segments", ["call_id", "offset_ms"])
    op.create_table(
        "recordings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="RECORDING", nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("waveform", sa.JSON(), nullable=True),
        sa.Column("download_url", sa.Text(), nullable=True),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('RECORDING', 'PROCESSING', 'DOWNLOADING', 'READY', 'ERROR')",
            name="ck_recordings_status",
        ),
    )
    op.create_index("ix_recordings_call_id", "recordings", ["call_id"])


def downgrade() -> None:
    op.drop_index("ix_recordings_call_id", table_name="recordings")
    op.drop_table("recordings")
    op.drop_index("ix_transcript_segments_call_offset", table_name="transcript_segments")
    op.drop_table("transcript_segments")
    op.drop_index("ix_call_events_organization_call", table_name="call_events")
    op.drop_table("call_events")
    op.drop_index("ix_calls_telnyx_call_control_id", table_name="calls")
    op.drop_column("calls", "next_sequence")
    op.drop_column("calls", "recording_offset_ms")
    op.drop_column("calls", "lifecycle_state")
    op.drop_column("calls", "call_session_id")
    op.drop_column("calls", "call_leg_id")
    op.drop_column("calls", "telnyx_call_control_id")
