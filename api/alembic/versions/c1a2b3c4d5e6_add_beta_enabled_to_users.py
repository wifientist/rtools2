"""Add beta_enabled to users

Revision ID: c1a2b3c4d5e6
Revises: b89c4d5e3f21
Create Date: 2025-11-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3c4d5e6'
down_revision: Union[str, None] = 'b89c4d5e3f21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add beta_enabled column to users table."""
    op.add_column('users',
        sa.Column('beta_enabled', sa.Boolean(), nullable=False, server_default='false')
    )


def downgrade() -> None:
    """Remove beta_enabled column from users table."""
    op.drop_column('users', 'beta_enabled')
