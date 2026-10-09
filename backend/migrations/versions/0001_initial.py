"""Create the initial empty schema.

This revision intentionally creates no business tables. It establishes a valid
Alembic head for the database plumbing phase.
"""

from __future__ import annotations

from typing import Sequence

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
