"""Tighten opportunity outcome and recovery timestamp invariants."""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0005_opp_timestamp_consistency"
down_revision: str | None = "0004_analytics_version"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_opportunities_won_at_status", "opportunities", type_="check")
    op.drop_constraint("ck_opportunities_lost_at_status", "opportunities", type_="check")
    op.drop_constraint("ck_opportunities_recovery_order", "opportunities", type_="check")
    op.create_check_constraint(
        "ck_opportunities_won_timestamp_consistent",
        "opportunities",
        "(status = 'won' AND won_at IS NOT NULL) OR (status <> 'won' AND won_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_opportunities_lost_timestamp_consistent",
        "opportunities",
        "(status = 'lost' AND lost_at IS NOT NULL) OR (status <> 'lost' AND lost_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_opportunities_recovery_order",
        "opportunities",
        "recovered_at IS NULL OR (recovery_started_at IS NOT NULL AND recovered_at >= recovery_started_at)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_opportunities_recovery_order", "opportunities", type_="check")
    op.drop_constraint("ck_opportunities_lost_timestamp_consistent", "opportunities", type_="check")
    op.drop_constraint("ck_opportunities_won_timestamp_consistent", "opportunities", type_="check")
    op.create_check_constraint(
        "ck_opportunities_recovery_order",
        "opportunities",
        "recovered_at IS NULL OR recovery_started_at IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_opportunities_lost_at_status",
        "opportunities",
        "lost_at IS NULL OR status = 'lost'",
    )
    op.create_check_constraint(
        "ck_opportunities_won_at_status",
        "opportunities",
        "won_at IS NULL OR status = 'won'",
    )
