"""Add migration dashboard snapshots

Revision ID: i7j8k9l0m1n2
Revises: h6i7j8k9l0m1
Create Date: 2026-02-24 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'i7j8k9l0m1n2'
down_revision: Union[str, None] = 'h6i7j8k9l0m1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create migration_dashboard_snapshots table."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'migration_dashboard_snapshots' not in existing_tables:
        op.create_table(
            'migration_dashboard_snapshots',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('controller_id', sa.Integer(),
                      sa.ForeignKey('controllers.id', ondelete='CASCADE'),
                      nullable=False),
            sa.Column('total_aps', sa.Integer(), nullable=False),
            sa.Column('operational_aps', sa.Integer(), nullable=False),
            sa.Column('total_venues', sa.Integer(), nullable=False),
            sa.Column('total_clients', sa.Integer(), nullable=False),
            sa.Column('total_ecs', sa.Integer(), nullable=False),
            sa.Column('tenant_data', sa.JSON(), nullable=False),
            sa.Column('captured_at', sa.DateTime(), nullable=False,
                      server_default=sa.func.now()),
        )
        op.create_index(
            'ix_snapshots_controller_captured',
            'migration_dashboard_snapshots',
            ['controller_id', 'captured_at'],
        )


def downgrade() -> None:
    """Drop migration_dashboard_snapshots table."""
    op.drop_index('ix_snapshots_controller_captured', table_name='migration_dashboard_snapshots')
    op.drop_table('migration_dashboard_snapshots')
