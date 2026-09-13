"""Initial scheduled change store schema

Revision ID: 0001
Revises:
Create Date: 2026-09-13

Column types come from ``scheduler/schema.py`` so the migration and the
metadata cannot drift apart on the cross-dialect details (UTC timestamps,
JSON/JSONB, and the autoincrementing surrogate key).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from dns_zone_manager.scheduler.schema import UtcDateTime, autoincrement_pk, json_column

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_changes",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("zone", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("scheduled_at", UtcDateTime(), nullable=True),
        sa.Column("not_valid_after", UtcDateTime(), nullable=True),
        sa.Column(
            "auto_prerequisites",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", UtcDateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("applied_at", UtcDateTime(), nullable=True),
        sa.Column("result_rcode", sa.Text(), nullable=True),
        sa.Column("new_serial", sa.BigInteger(), nullable=True),
        sa.Column("reverted_at", UtcDateTime(), nullable=True),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", UtcDateTime(), nullable=True),
        sa.Column("source", sa.Text(), server_default="scheduler", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_changes")),
    )
    op.create_index("idx_scheduled_changes_status", "scheduled_changes", ["status"])
    op.create_index("idx_scheduled_changes_zone", "scheduled_changes", ["zone"])
    op.create_index("idx_scheduled_changes_due", "scheduled_changes", ["status", "scheduled_at"])
    op.create_index("idx_scheduled_changes_source", "scheduled_changes", ["source"])

    op.create_table(
        "scheduled_operations",
        sa.Column("change_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("rdclass", sa.Text(), server_default="IN", nullable=False),
        sa.Column("ttl", sa.Integer(), server_default="3600", nullable=False),
        sa.Column("records", json_column(), nullable=True),
        sa.Column("prior_ttl", sa.Integer(), nullable=True),
        sa.Column("prior_records", json_column(), nullable=True),
        sa.Column("snapshot_at", UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["change_id"],
            ["scheduled_changes.id"],
            name=op.f("fk_scheduled_operations_change_id_scheduled_changes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("change_id", "seq", name=op.f("pk_scheduled_operations")),
    )

    op.create_table(
        "scheduled_prerequisites",
        sa.Column("change_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("prereq_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("rdtype", sa.Text(), nullable=True),
        sa.Column("rdclass", sa.Text(), server_default="IN", nullable=False),
        sa.Column("data", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["change_id"],
            ["scheduled_changes.id"],
            name=op.f("fk_scheduled_prerequisites_change_id_scheduled_changes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("change_id", "seq", name=op.f("pk_scheduled_prerequisites")),
    )

    op.create_table(
        "scheduled_change_events",
        sa.Column("id", autoincrement_pk(), autoincrement=True, nullable=False),
        sa.Column("change_id", sa.Text(), nullable=False),
        sa.Column("ts", UtcDateTime(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("detail", json_column(), nullable=True),
        sa.ForeignKeyConstraint(
            ["change_id"],
            ["scheduled_changes.id"],
            name=op.f("fk_scheduled_change_events_change_id_scheduled_changes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_change_events")),
    )
    op.create_index("idx_scheduled_change_events_change", "scheduled_change_events", ["change_id"])
    op.create_index("idx_scheduled_change_events_event", "scheduled_change_events", ["event"])
    op.create_index("idx_scheduled_change_events_actor", "scheduled_change_events", ["actor"])
    op.create_index(
        "idx_scheduled_change_events_ts",
        "scheduled_change_events",
        [sa.text("ts DESC")],
    )


def downgrade() -> None:
    op.drop_table("scheduled_change_events")
    op.drop_table("scheduled_prerequisites")
    op.drop_table("scheduled_operations")
    op.drop_table("scheduled_changes")
