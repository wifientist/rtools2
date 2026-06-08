"""Add danger_enabled to users

Revision ID: r9s0t1u2v3w4
Revises: c0436d9e3e15
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'r9s0t1u2v3w4'
down_revision: Union[str, None] = 'c0436d9e3e15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add danger_enabled column to users table."""
    op.add_column('users',
        sa.Column('danger_enabled', sa.Boolean(), nullable=False, server_default='false')
    )


def downgrade() -> None:
    """Remove danger_enabled column from users table."""
    op.drop_column('users', 'danger_enabled')
