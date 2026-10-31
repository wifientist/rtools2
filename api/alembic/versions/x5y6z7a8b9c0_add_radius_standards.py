"""Add radius_standards

Revision ID: x5y6z7a8b9c0
Revises: w4x5y6z7a8b9
Create Date: 2026-10-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'x5y6z7a8b9c0'
down_revision: Union[str, None] = 'w4x5y6z7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A customer's standard set of RADIUS attribute groups."""
    from sqlalchemy import inspect
    if 'radius_standards' in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'radius_standards',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('groups', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('user_id', 'name', name='uq_radius_standards_user_name'),
    )


def downgrade() -> None:
    op.drop_table('radius_standards')
