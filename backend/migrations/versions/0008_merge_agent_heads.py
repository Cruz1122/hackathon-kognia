"""Merge the parallel agent migration branches."""

from __future__ import annotations

from typing import Sequence


revision: str = "0008_merge_agent_heads"
down_revision: tuple[str, str] = ("0007_agent_traces", "0007_stateful_agent")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Join both 0007 branches without changing the database schema."""


def downgrade() -> None:
    """Re-expose both 0007 heads when rolling back the merge point."""
