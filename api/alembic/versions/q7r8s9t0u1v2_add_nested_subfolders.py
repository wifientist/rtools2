"""Add parent_subfolder_id for nested subfolder support

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-03-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'q7r8s9t0u1v2'
down_revision: Union[str, None] = 'p6q7r8s9t0u1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    columns = [c["name"] for c in inspector.get_columns("file_subfolders")]
    if "parent_subfolder_id" not in columns:
        op.add_column(
            "file_subfolders",
            sa.Column(
                "parent_subfolder_id",
                sa.Integer(),
                sa.ForeignKey("file_subfolders.id", ondelete="CASCADE"),
                nullable=True,
                index=True,
            ),
        )

    # Replace old unique constraint with one that includes parent_subfolder_id
    # Drop old constraint, create new one
    try:
        op.drop_constraint("uq_subfolder_slug", "file_subfolders", type_="unique")
    except Exception:
        pass  # May not exist if tables were recreated

    op.create_unique_constraint(
        "uq_subfolder_slug",
        "file_subfolders",
        ["folder_id", "parent_subfolder_id", "slug"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_subfolder_slug", "file_subfolders", type_="unique")
    op.drop_column("file_subfolders", "parent_subfolder_id")
    op.create_unique_constraint(
        "uq_subfolder_slug",
        "file_subfolders",
        ["folder_id", "slug"],
    )
