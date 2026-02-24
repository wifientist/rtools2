"""Add migration dashboard settings

Revision ID: h6i7j8k9l0m1
Revises: g5h6i7j8k9l0
Create Date: 2026-02-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'h6i7j8k9l0m1'
down_revision: Union[str, None] = 'g5h6i7j8k9l0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create migration_dashboard_settings table."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'migration_dashboard_settings' not in existing_tables:
        op.create_table(
            'migration_dashboard_settings',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('controller_id', sa.Integer(),
                      sa.ForeignKey('controllers.id', ondelete='CASCADE'),
                      nullable=False, unique=True),
            sa.Column('target_aps', sa.Integer(), nullable=False, server_default='180000'),
            sa.Column('ignored_tenant_ids', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                      server_default=sa.func.now()),
        )


def downgrade() -> None:
    """Drop migration_dashboard_settings table."""
    op.drop_table('migration_dashboard_settings')
