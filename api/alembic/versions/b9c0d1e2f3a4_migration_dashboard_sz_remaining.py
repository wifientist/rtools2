"""Migration dashboard: SZ controllers to count, and SZ connected APs / online switches per snapshot

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9c0d1e2f3a4'
down_revision: Union[str, None] = 'a8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    insp = inspect(op.get_bind())
    settings_cols = [c['name'] for c in insp.get_columns('migration_dashboard_settings')]
    if 'sz_controller_ids' not in settings_cols:
        op.add_column(
            'migration_dashboard_settings',
            sa.Column('sz_controller_ids', sa.JSON(), nullable=False, server_default='[]'),
        )
    snapshot_cols = [c['name'] for c in insp.get_columns('migration_dashboard_snapshots')]
    if 'sz_connected_aps' not in snapshot_cols:
        op.add_column(
            'migration_dashboard_snapshots',
            sa.Column('sz_connected_aps', sa.Integer(), nullable=True),
        )
    if 'sz_online_switches' not in snapshot_cols:
        op.add_column(
            'migration_dashboard_snapshots',
            sa.Column('sz_online_switches', sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column('migration_dashboard_snapshots', 'sz_online_switches')
    op.drop_column('migration_dashboard_snapshots', 'sz_connected_aps')
    op.drop_column('migration_dashboard_settings', 'sz_controller_ids')
