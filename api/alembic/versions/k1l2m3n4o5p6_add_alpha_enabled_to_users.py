"""Add alpha_enabled to users

Revision ID: k1l2m3n4o5p6
Revises: i7j8k9l0m1n2
Create Date: 2026-02-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'k1l2m3n4o5p6'
down_revision: Union[str, None] = 'i7j8k9l0m1n2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add alpha_enabled column to users table."""
    op.add_column('users',
        sa.Column('alpha_enabled', sa.Boolean(), nullable=False, server_default='false')
    )


def downgrade() -> None:
    """Remove alpha_enabled column from users table."""
    op.drop_column('users', 'alpha_enabled')
