"""Add company approval fields

Revision ID: b89c4d5e3f21
Revises: a54f7a0a2900
Create Date: 2025-11-13 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b89c4d5e3f21'
down_revision: Union[str, None] = 'a54f7a0a2900'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_approved and created_at columns to companies table."""
    # Add is_approved column (defaults to False for security)
    op.add_column('companies',
        sa.Column('is_approved', sa.Boolean(), nullable=False, server_default='false')
    )

    # Add created_at column for audit trail
    op.add_column('companies',
        sa.Column('created_at', sa.DateTime(), nullable=True)
    )

    # IMPORTANT: Approve the "Unassigned" company (id=-1) by default
    # This ensures admins can always be assigned to the Unassigned company
    op.execute(
        "UPDATE companies SET is_approved = true WHERE id = -1"
    )


def downgrade() -> None:
    """Remove is_approved and created_at columns from companies table."""
    op.drop_column('companies', 'created_at')
    op.drop_column('companies', 'is_approved')
