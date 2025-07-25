"""add ec_type to Tenant model

Revision ID: 37499b6a6f73
Revises: f3bf533425b6
Create Date: 2025-06-11 11:01:56.228980

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '37499b6a6f73'
down_revision: Union[str, None] = 'f3bf533425b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('tenants', sa.Column('ec_type', sa.String(), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('tenants', 'ec_type')
    # ### end Alembic commands ###
