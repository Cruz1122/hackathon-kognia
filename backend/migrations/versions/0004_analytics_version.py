"""Persist analytics cache versions with the PostgreSQL source of truth."""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0004_analytics_version"
down_revision: str | None = "0003_commercial_model"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("analytics_version", sa.BigInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("organizations", "analytics_version")
