"""Add retention lookup index on scheduled_changes

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13

Supports age- and size-based retention queries that filter by status and
order by completion time (updated_at is the cheapest proxy in the index;
the purge SQL still uses COALESCE for the true completion timestamp).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_scheduled_changes_retention",
        "scheduled_changes",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_scheduled_changes_retention", table_name="scheduled_changes")
