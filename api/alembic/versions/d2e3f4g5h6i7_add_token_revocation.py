"""Add token revocation table

Revision ID: d2e3f4g5h6i7
Revises: c1a2b3c4d5e6
Create Date: 2025-11-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4g5h6i7'
down_revision: Union[str, None] = 'c1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create token revocation table for tracking invalidated tokens."""
    # Check if table exists before creating
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)

    if 'revoked_tokens' not in inspector.get_table_names():
        op.create_table(
            'revoked_tokens',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('jti', sa.String(), nullable=False, unique=True, index=True),  # JWT ID
            sa.Column('token_type', sa.String(), nullable=False),  # 'access' or 'refresh'
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),  # Original expiry
            sa.Column('revoked_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),  # Admin who revoked it
            sa.Column('reason', sa.String(), nullable=True),  # Optional revocation reason
        )

        # Create index for efficient cleanup of expired tokens
        op.create_index('ix_revoked_tokens_expires_at', 'revoked_tokens', ['expires_at'])


def downgrade() -> None:
    """Remove token revocation table."""
    op.drop_index('ix_revoked_tokens_expires_at', table_name='revoked_tokens')
    op.drop_table('revoked_tokens')
