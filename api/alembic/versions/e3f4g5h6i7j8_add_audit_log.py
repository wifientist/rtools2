"""Add audit log table

Revision ID: e3f4g5h6i7j8
Revises: d2e3f4g5h6i7
Create Date: 2025-11-22 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f4g5h6i7j8'
down_revision: Union[str, None] = 'd2e3f4g5h6i7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create audit log table for tracking sensitive operations."""
    # Check if table exists before creating
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)

    if 'audit_logs' not in inspector.get_table_names():
        op.create_table(
            'audit_logs',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('timestamp', sa.DateTime(), nullable=False),
            sa.Column('action', sa.String(), nullable=False),  # e.g., 'role_change', 'token_revoked'
            sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),  # Who performed action
            sa.Column('target_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),  # Who was affected
            sa.Column('details', sa.JSON(), nullable=True),  # Additional context
            sa.Column('ip_address', sa.String(), nullable=True),
            sa.Column('user_agent', sa.String(), nullable=True),
        )

        # Create indexes for common queries
        op.create_index('ix_audit_logs_timestamp', 'audit_logs', ['timestamp'])
        op.create_index('ix_audit_logs_action', 'audit_logs', ['action'])
        op.create_index('ix_audit_logs_actor_id', 'audit_logs', ['actor_id'])
        op.create_index('ix_audit_logs_target_user_id', 'audit_logs', ['target_user_id'])


def downgrade() -> None:
    """Remove audit log table."""
    op.drop_index('ix_audit_logs_target_user_id', table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor_id', table_name='audit_logs')
    op.drop_index('ix_audit_logs_action', table_name='audit_logs')
    op.drop_index('ix_audit_logs_timestamp', table_name='audit_logs')
    op.drop_table('audit_logs')
