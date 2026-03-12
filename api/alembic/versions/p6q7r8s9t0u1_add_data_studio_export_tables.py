"""Add data_studio_export_configs and data_studio_export_runs tables

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-03-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'p6q7r8s9t0u1'
down_revision: Union[str, None] = 'o5p6q7r8s9t0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if "data_studio_export_configs" not in existing_tables:
        op.create_table(
            "data_studio_export_configs",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("encrypted_web_username", sa.String(), nullable=False),
            sa.Column("encrypted_web_password", sa.String(), nullable=False),
            sa.Column("report_name", sa.String(255), nullable=False),
            sa.Column("tenant_configs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("retention_count", sa.Integer(), nullable=False, server_default="24"),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if "data_studio_export_runs" not in existing_tables:
        op.create_table(
            "data_studio_export_runs",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("data_studio_export_configs.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("tenant_id", sa.String(), nullable=False, index=True),
            sa.Column("tenant_name", sa.String(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("screenshot_s3_key", sa.String(500), nullable=True),
            sa.Column("s3_key", sa.String(500), nullable=True),
            sa.Column("shared_file_id", sa.Integer(), nullable=True),
            sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("filename", sa.String(255), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("duration_seconds", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("data_studio_export_runs")
    op.drop_table("data_studio_export_configs")
