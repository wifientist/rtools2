"""Add tracker_sites for the Migration Tracker board

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-10-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'v3w4x5y6z7a8'
down_revision: Union[str, None] = 'u2v3w4x5y6z7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create tracker_sites table."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    if 'tracker_sites' in inspect(bind).get_table_names():
        return

    op.create_table(
        'tracker_sites',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('site_name', sa.String(), nullable=False),
        sa.Column('vsz', sa.String(), nullable=True),
        sa.Column('tenant', sa.String(), nullable=True),
        sa.Column('sz_ap_count', sa.Integer(), nullable=True),
        sa.Column('sz_wlan_count', sa.Integer(), nullable=True),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('config_pushed', sa.Date(), nullable=True),
        sa.Column('config_pushed_done', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('validation', sa.Date(), nullable=True),
        sa.Column('validation_done', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('hw_migrated', sa.Date(), nullable=True),
        sa.Column('hw_migrated_done', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('events', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_by', sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Drop tracker_sites table."""
    op.drop_table('tracker_sites')
