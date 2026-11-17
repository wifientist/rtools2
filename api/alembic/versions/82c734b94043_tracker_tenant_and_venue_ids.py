"""Migration Tracker: R1 tenant id and venue id per site

Revision ID: 82c734b94043
Revises: f7c3a91e2b04
Create Date: 2026-11-17 00:00:00.000000

r1_tenant_id is copied from the controller picked as the site's tenant;
venue_id is typed in by hand.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '82c734b94043'
down_revision: Union[str, None] = 'f7c3a91e2b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    cols = [c['name'] for c in inspect(op.get_bind()).get_columns('tracker_sites')]
    for name in ('r1_tenant_id', 'venue_id'):
        if name not in cols:
            op.add_column('tracker_sites', sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('tracker_sites', 'venue_id')
    op.drop_column('tracker_sites', 'r1_tenant_id')
