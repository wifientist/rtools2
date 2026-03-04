"""Add venue_data to migration dashboard snapshots

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-03-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "n4o5p6q7r8s9"
down_revision: Union[str, None] = "m3n4o5p6q7r8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add venue_data JSON column to snapshots table."""
    op.add_column(
        "migration_dashboard_snapshots",
        sa.Column("venue_data", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Remove venue_data column."""
    op.drop_column("migration_dashboard_snapshots", "venue_data")
