"""Per-user Slack webhook and which job events it receives

Revision ID: z7a8b9c0d1e2
Revises: y6z7a8b9c0d1
Create Date: 2026-10-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'z7a8b9c0d1e2'
down_revision: Union[str, None] = 'y6z7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Nothing is sent until a user saves a webhook, so the events default on."""
    from sqlalchemy import inspect
    columns = [c['name'] for c in inspect(op.get_bind()).get_columns('users')]
    if 'encrypted_slack_webhook' not in columns:
        op.add_column('users', sa.Column('encrypted_slack_webhook', sa.String(), nullable=True))
    for name in ('slack_notify_completed', 'slack_notify_failed', 'slack_notify_confirmation'):
        if name not in columns:
            op.add_column('users', sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column('users', 'slack_notify_confirmation')
    op.drop_column('users', 'slack_notify_failed')
    op.drop_column('users', 'slack_notify_completed')
    op.drop_column('users', 'encrypted_slack_webhook')
