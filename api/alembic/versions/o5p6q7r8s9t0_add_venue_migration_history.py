"""Add venue_migration_history table and drop venue_data JSON column

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-03-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "o5p6q7r8s9t0"
down_revision: Union[str, None] = "n4o5p6q7r8s9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create venue_migration_history table, drop venue_data column."""
    op.create_table(
        "venue_migration_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "controller_id",
            sa.Integer(),
            sa.ForeignKey("controllers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("venue_name", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("tenant_name", sa.String(), nullable=False),
        sa.Column("ap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operational", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="Pending"),
        sa.Column("pending_at", sa.DateTime(), nullable=True),
        sa.Column("in_progress_at", sa.DateTime(), nullable=True),
        sa.Column("migrated_at", sa.DateTime(), nullable=True),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_venue_migration_history_id", "venue_migration_history", ["id"])
    op.create_unique_constraint(
        "uq_controller_venue", "venue_migration_history", ["controller_id", "venue_id"]
    )
    op.create_index(
        "ix_vmh_controller_status", "venue_migration_history", ["controller_id", "status"]
    )

    op.drop_column("migration_dashboard_snapshots", "venue_data")


def downgrade() -> None:
    """Drop venue_migration_history table, re-add venue_data column."""
    op.add_column(
        "migration_dashboard_snapshots",
        sa.Column("venue_data", sa.JSON(), nullable=True),
    )
    op.drop_table("venue_migration_history")
