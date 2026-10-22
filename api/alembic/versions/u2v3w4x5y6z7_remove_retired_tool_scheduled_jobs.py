"""Remove scheduled jobs for retired tools (Pop and Swap, DPSK Orchestrator, DFS Blacklist)

The scheduler reloads every enabled scheduled_jobs row at startup, so rows
pointing at the deleted modules would keep firing and failing on import.
Their run history goes with them (ON DELETE CASCADE).

The tools' own data tables are left in place.

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-10-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'u2v3w4x5y6z7'
down_revision: Union[str, None] = 't1u2v3w4x5y6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM scheduled_jobs WHERE "
        "callable_path LIKE 'routers.ap_pop_swap.%' OR "
        "callable_path LIKE 'routers.orchestrator.%' OR "
        "callable_path LIKE 'jobs.dfs_blacklist_job:%'"
    )


def downgrade() -> None:
    # The modules these jobs called no longer exist.
    pass
