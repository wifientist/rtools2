"""Add encrypted_portal_key to controllers

Revision ID: w4x5y6z7a8b9
Revises: v3w4x5y6z7a8
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'w4x5y6z7a8b9'
down_revision: Union[str, None] = 'v3w4x5y6z7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The customer's captive portal key, stored per RuckusONE controller."""
    from sqlalchemy import inspect
    columns = [c['name'] for c in inspect(op.get_bind()).get_columns('controllers')]
    if 'encrypted_portal_key' in columns:
        return
    op.add_column('controllers', sa.Column('encrypted_portal_key', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('controllers', 'encrypted_portal_key')
