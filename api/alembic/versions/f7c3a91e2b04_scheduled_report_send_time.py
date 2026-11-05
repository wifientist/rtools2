"""Scheduled reports: per-schedule send time and time zone

Revision ID: f7c3a91e2b04
Revises: d1e2f3a4b5c6
Create Date: 2026-11-05 00:00:00.000000

Existing schedules get 12:30 UTC, the time the dispatcher used to send at, so
nothing moves until someone picks a local time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7c3a91e2b04'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    cols = [c['name'] for c in inspect(op.get_bind()).get_columns('scheduled_reports')]
    if 'send_time' not in cols:
        op.add_column(
            'scheduled_reports',
            sa.Column('send_time', sa.String(5), nullable=False, server_default='12:30'),
        )
    if 'timezone' not in cols:
        op.add_column(
            'scheduled_reports',
            sa.Column('timezone', sa.String(64), nullable=False, server_default='UTC'),
        )


def downgrade() -> None:
    op.drop_column('scheduled_reports', 'timezone')
    op.drop_column('scheduled_reports', 'send_time')
