"""Add switch counts to migration dashboard snapshots

Revision ID: t1u2v3w4x5y6
Revises: s0t1u2v3w4x5
Create Date: 2026-06-08 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 't1u2v3w4x5y6'
down_revision: Union[str, None] = 's0t1u2v3w4x5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add total_switches / operational_switches columns to snapshots."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('migration_dashboard_snapshots')]

    if 'total_switches' not in columns:
        op.add_column(
            'migration_dashboard_snapshots',
            sa.Column('total_switches', sa.Integer(), nullable=False, server_default='0'),
        )
    if 'operational_switches' not in columns:
        op.add_column(
            'migration_dashboard_snapshots',
            sa.Column('operational_switches', sa.Integer(), nullable=False, server_default='0'),
        )


def downgrade() -> None:
    """Remove switch count columns from snapshots."""
    op.drop_column('migration_dashboard_snapshots', 'operational_switches')
    op.drop_column('migration_dashboard_snapshots', 'total_switches')
