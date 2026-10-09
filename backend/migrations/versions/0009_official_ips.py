"""Persist versioned official IPS snapshots and installed capacities."""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0009_official_ips"
down_revision: str | None = "0008_merge_agent_heads"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ips_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="staging", nullable=False),
        sa.Column("source_row_count", sa.Integer(), nullable=False),
        sa.Column("site_count", sa.Integer(), nullable=False),
        sa.Column("cutoff_values", sa.JSON(), nullable=False),
        sa.Column("source_values", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('staging', 'active', 'inactive', 'failed')",
            name="ck_ips_snapshots_status",
        ),
        sa.CheckConstraint("source_row_count > 0", name="ck_ips_snapshots_source_rows_positive"),
        sa.CheckConstraint("site_count > 0", name="ck_ips_snapshots_sites_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_hash"),
    )
    op.create_index(
        "ix_ips_snapshots_status_created_at",
        "ips_snapshots",
        ["status", "created_at"],
    )
    op.create_index(
        "uq_ips_snapshots_one_active",
        "ips_snapshots",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "ips_sites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("site_code", sa.String(length=32), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("provider_name", sa.String(length=500), nullable=False),
        sa.Column("nit", sa.String(length=32), nullable=True),
        sa.Column("verification_digit", sa.String(length=8), nullable=True),
        sa.Column("nature", sa.String(length=64), nullable=True),
        sa.Column("care_level", sa.String(length=32), nullable=True),
        sa.Column("site_number", sa.String(length=32), nullable=False),
        sa.Column("site_name", sa.String(length=500), nullable=False),
        sa.Column("manager", sa.String(length=500), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("email", sa.String(length=500), nullable=True),
        sa.Column("phone", sa.String(length=255), nullable=True),
        sa.Column("department", sa.String(length=128), nullable=False),
        sa.Column("municipality", sa.String(length=128), nullable=False),
        sa.Column("cutoff", sa.String(length=255), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["ips_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "site_code", name="uq_ips_sites_snapshot_site_code"),
    )
    op.create_index(
        "ix_ips_sites_snapshot_department_municipality",
        "ips_sites",
        ["snapshot_id", "department", "municipality"],
    )
    op.create_index("ix_ips_sites_snapshot_name", "ips_sites", ["snapshot_id", "site_name"])
    op.create_index(
        "ix_ips_sites_snapshot_provider_name",
        "ips_sites",
        ["snapshot_id", "provider_name"],
    )

    op.create_table(
        "ips_capacities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("group_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("raw_record", sa.JSON(), nullable=False),
        sa.Column("source_row_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_ips_capacities_quantity_nonnegative"),
        sa.ForeignKeyConstraint(["site_id"], ["ips_sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "site_id",
            "source_row_hash",
            name="uq_ips_capacities_site_source_row",
        ),
    )
    op.create_index("ix_ips_capacities_site_id", "ips_capacities", ["site_id"])
    op.create_index(
        "ix_ips_capacities_group_description",
        "ips_capacities",
        ["group_name", "description"],
    )


def downgrade() -> None:
    op.drop_index("ix_ips_capacities_group_description", table_name="ips_capacities")
    op.drop_index("ix_ips_capacities_site_id", table_name="ips_capacities")
    op.drop_table("ips_capacities")
    op.drop_index("ix_ips_sites_snapshot_provider_name", table_name="ips_sites")
    op.drop_index("ix_ips_sites_snapshot_name", table_name="ips_sites")
    op.drop_index("ix_ips_sites_snapshot_department_municipality", table_name="ips_sites")
    op.drop_table("ips_sites")
    op.drop_index("uq_ips_snapshots_one_active", table_name="ips_snapshots")
    op.drop_index("ix_ips_snapshots_status_created_at", table_name="ips_snapshots")
    op.drop_table("ips_snapshots")
