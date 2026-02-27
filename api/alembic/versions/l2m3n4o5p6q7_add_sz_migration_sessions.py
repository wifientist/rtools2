"""Add sz_migration_sessions table

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-02-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'l2m3n4o5p6q7'
down_revision: Union[str, None] = 'k1l2m3n4o5p6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create sz_migration_sessions table."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'sz_migration_sessions' not in existing_tables:
        op.create_table(
            'sz_migration_sessions',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('user_id', sa.Integer(),
                      sa.ForeignKey('users.id', ondelete='CASCADE'),
                      nullable=False, index=True),
            sa.Column('status', sa.String(), nullable=False, server_default='draft'),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                      server_default=sa.func.now()),

            # Source (SZ)
            sa.Column('sz_controller_id', sa.Integer(),
                      sa.ForeignKey('controllers.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('sz_domain_id', sa.String(), nullable=True),
            sa.Column('sz_zone_id', sa.String(), nullable=True),
            sa.Column('sz_zone_name', sa.String(), nullable=True),

            # Destination (R1)
            sa.Column('r1_controller_id', sa.Integer(),
                      sa.ForeignKey('controllers.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('r1_tenant_id', sa.String(), nullable=True),
            sa.Column('r1_venue_id', sa.String(), nullable=True),
            sa.Column('r1_venue_name', sa.String(), nullable=True),

            # Job references
            sa.Column('extraction_job_id', sa.String(), nullable=True),
            sa.Column('r1_snapshot_job_id', sa.String(), nullable=True),
            sa.Column('plan_job_id', sa.String(), nullable=True),
            sa.Column('execution_job_id', sa.String(), nullable=True),

            # Cached summary
            sa.Column('current_step', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('wlan_count', sa.Integer(), nullable=True),
            sa.Column('summary_json', sa.JSON(), nullable=True),
        )
        # Composite index for "my active migrations" queries
        op.create_index(
            'ix_sz_migration_sessions_user_status',
            'sz_migration_sessions',
            ['user_id', 'status'],
        )
        op.create_index(
            'ix_sz_migration_sessions_created',
            'sz_migration_sessions',
            ['created_at'],
        )


def downgrade() -> None:
    """Drop sz_migration_sessions table."""
    op.drop_index('ix_sz_migration_sessions_created',
                  table_name='sz_migration_sessions')
    op.drop_index('ix_sz_migration_sessions_user_status',
                  table_name='sz_migration_sessions')
    op.drop_table('sz_migration_sessions')
