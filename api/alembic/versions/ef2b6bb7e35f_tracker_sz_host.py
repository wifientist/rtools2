"""Migration Tracker: SmartZone controller host per site

Revision ID: ef2b6bb7e35f
Revises: 82c734b94043
Create Date: 2026-11-17 00:00:00.000000

sz_host is copied from the SmartZone controller picked as the site's vSZ.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef2b6bb7e35f'
down_revision: Union[str, None] = '82c734b94043'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    cols = [c['name'] for c in inspect(op.get_bind()).get_columns('tracker_sites')]
    if 'sz_host' not in cols:
        op.add_column('tracker_sites', sa.Column('sz_host', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('tracker_sites', 'sz_host')
