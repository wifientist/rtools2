"""Record where a RADIUS standard's values came from

Revision ID: y6z7a8b9c0d1
Revises: x5y6z7a8b9c0
Create Date: 2026-10-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'y6z7a8b9c0d1'
down_revision: Union[str, None] = 'x5y6z7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The tenant a template was taken from, shown long after it was chosen."""
    from sqlalchemy import inspect
    columns = [c['name'] for c in inspect(op.get_bind()).get_columns('radius_standards')]
    if 'source' not in columns:
        op.add_column('radius_standards', sa.Column('source', sa.String(), nullable=True))
    if 'source_tenant_id' not in columns:
        op.add_column('radius_standards', sa.Column('source_tenant_id', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('radius_standards', 'source_tenant_id')
    op.drop_column('radius_standards', 'source')
