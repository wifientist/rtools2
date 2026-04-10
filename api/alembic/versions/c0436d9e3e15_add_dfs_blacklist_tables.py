"""add dfs blacklist tables

Revision ID: c0436d9e3e15
Revises: q7r8s9t0u1v2
Create Date: 2026-03-27 20:50:43.268332

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c0436d9e3e15'
down_revision: Union[str, None] = 'q7r8s9t0u1v2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create DFS blacklist tables."""
    op.create_table(
        'dfs_blacklist_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('controller_id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('zones', sa.JSON(), nullable=False),
        sa.Column('ap_groups', sa.JSON(), nullable=False),
        sa.Column('thresholds', sa.JSON(), nullable=False),
        sa.Column('event_filters', sa.JSON(), nullable=True),
        sa.Column('encrypted_slack_webhook_url', sa.String(length=500), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['controller_id'], ['controllers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dfs_blacklist_configs_id', 'dfs_blacklist_configs', ['id'])
    op.create_index('ix_dfs_blacklist_configs_controller_id', 'dfs_blacklist_configs', ['controller_id'])

    op.create_table(
        'dfs_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('sz_event_id', sa.String(), nullable=True),
        sa.Column('event_code', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(), nullable=True),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('severity', sa.String(), nullable=True),
        sa.Column('activity', sa.Text(), nullable=True),
        sa.Column('channel', sa.Integer(), nullable=True),
        sa.Column('zone_id', sa.String(), nullable=True),
        sa.Column('zone_name', sa.String(), nullable=True),
        sa.Column('ap_group_id', sa.String(), nullable=True),
        sa.Column('ap_group_name', sa.String(), nullable=True),
        sa.Column('ap_mac', sa.String(), nullable=True),
        sa.Column('ap_name', sa.String(), nullable=True),
        sa.Column('event_timestamp', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('raw_data', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['config_id'], ['dfs_blacklist_configs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dfs_events_id', 'dfs_events', ['id'])
    op.create_index('ix_dfs_events_config_id', 'dfs_events', ['config_id'])
    op.create_index('ix_dfs_events_sz_event_id', 'dfs_events', ['sz_event_id'])
    op.create_index('ix_dfs_events_config_timestamp', 'dfs_events', ['config_id', 'event_timestamp'])

    op.create_table(
        'dfs_blacklist_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.Integer(), nullable=False),
        sa.Column('zone_id', sa.String(), nullable=True),
        sa.Column('zone_name', sa.String(), nullable=True),
        sa.Column('ap_group_id', sa.String(), nullable=True),
        sa.Column('ap_group_name', sa.String(), nullable=True),
        sa.Column('threshold_type', sa.String(length=10), nullable=False),
        sa.Column('event_count', sa.Integer(), nullable=False),
        sa.Column('blacklisted_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('reentry_at', sa.DateTime(), nullable=False),
        sa.Column('reentry_completed_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
        sa.ForeignKeyConstraint(['config_id'], ['dfs_blacklist_configs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dfs_blacklist_entries_id', 'dfs_blacklist_entries', ['id'])
    op.create_index('ix_dfs_blacklist_entries_config_id', 'dfs_blacklist_entries', ['config_id'])
    op.create_index('ix_dfs_blacklist_entries_status', 'dfs_blacklist_entries', ['status'])

    op.create_table(
        'dfs_audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['config_id'], ['dfs_blacklist_configs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dfs_audit_logs_id', 'dfs_audit_logs', ['id'])
    op.create_index('ix_dfs_audit_logs_config_id', 'dfs_audit_logs', ['config_id'])


def downgrade() -> None:
    """Drop DFS blacklist tables."""
    op.drop_table('dfs_audit_logs')
    op.drop_table('dfs_blacklist_entries')
    op.drop_table('dfs_events')
    op.drop_table('dfs_blacklist_configs')
