"""Migration dashboard: minimum online devices for a zone / switch group to count

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c0d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    cols = [c['name'] for c in inspect(op.get_bind()).get_columns('migration_dashboard_settings')]
    if 'sz_min_zone_aps' not in cols:
        op.add_column(
            'migration_dashboard_settings',
            sa.Column('sz_min_zone_aps', sa.Integer(), nullable=False, server_default='5'),
        )
    if 'sz_min_group_switches' not in cols:
        op.add_column(
            'migration_dashboard_settings',
            sa.Column('sz_min_group_switches', sa.Integer(), nullable=False, server_default='1'),
        )


def downgrade() -> None:
    op.drop_column('migration_dashboard_settings', 'sz_min_group_switches')
    op.drop_column('migration_dashboard_settings', 'sz_min_zone_aps')
