"""Add target_switches to migration dashboard settings

Revision ID: s0t1u2v3w4x5
Revises: r9s0t1u2v3w4
Create Date: 2026-06-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 's0t1u2v3w4x5'
down_revision: Union[str, None] = 'r9s0t1u2v3w4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add target_switches column to migration_dashboard_settings."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('migration_dashboard_settings')]

    if 'target_switches' not in columns:
        op.add_column(
            'migration_dashboard_settings',
            sa.Column('target_switches', sa.Integer(), nullable=False, server_default='10000'),
        )


def downgrade() -> None:
    """Remove target_switches column from migration_dashboard_settings."""
    op.drop_column('migration_dashboard_settings', 'target_switches')
