"""Add fileshare tables

Revision ID: g5h6i7j8k9l0
Revises: 144fcbb9c5e2
Create Date: 2026-02-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g5h6i7j8k9l0'
down_revision: Union[str, None] = '144fcbb9c5e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create fileshare tables."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()

    # Create file_folders table
    if 'file_folders' not in existing_tables:
        op.create_table(
            'file_folders',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('name', sa.String(100), nullable=False),
            sa.Column('slug', sa.String(50), nullable=False, unique=True),
            sa.Column('description', sa.String(500), nullable=True),
            sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id'), nullable=True),
            sa.Column('quota_bytes', sa.BigInteger(), nullable=False, default=10737418240),  # 10GB
            sa.Column('used_bytes', sa.BigInteger(), nullable=False, default=0),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_file_folders_company_id', 'file_folders', ['company_id'])

    # Create file_subfolders table
    if 'file_subfolders' not in existing_tables:
        op.create_table(
            'file_subfolders',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('folder_id', sa.Integer(), sa.ForeignKey('file_folders.id', ondelete='CASCADE'), nullable=False),
            sa.Column('name', sa.String(100), nullable=False),
            sa.Column('slug', sa.String(50), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('folder_id', 'slug', name='uq_subfolder_slug'),
        )
        op.create_index('ix_file_subfolders_folder_id', 'file_subfolders', ['folder_id'])

    # Create folder_permissions table
    if 'folder_permissions' not in existing_tables:
        op.create_table(
            'folder_permissions',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('folder_id', sa.Integer(), sa.ForeignKey('file_folders.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('permission_type', sa.String(20), nullable=False),
            sa.Column('granted_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('granted_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('folder_id', 'user_id', 'permission_type', name='uq_folder_user_perm'),
        )
        op.create_index('ix_folder_permissions_folder_id', 'folder_permissions', ['folder_id'])
        op.create_index('ix_folder_permissions_user_id', 'folder_permissions', ['user_id'])

    # Create shared_files table
    if 'shared_files' not in existing_tables:
        op.create_table(
            'shared_files',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('folder_id', sa.Integer(), sa.ForeignKey('file_folders.id', ondelete='CASCADE'), nullable=False),
            sa.Column('subfolder_id', sa.Integer(), sa.ForeignKey('file_subfolders.id', ondelete='CASCADE'), nullable=True),
            sa.Column('filename', sa.String(255), nullable=False),
            sa.Column('s3_key', sa.String(500), nullable=False),
            sa.Column('size_bytes', sa.BigInteger(), nullable=False),
            sa.Column('content_type', sa.String(100), nullable=False),
            sa.Column('upload_status', sa.String(20), nullable=False, default='pending'),
            sa.Column('s3_upload_id', sa.String(200), nullable=True),
            sa.Column('uploaded_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('uploaded_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('download_count', sa.Integer(), nullable=False, default=0),
        )
        op.create_index('ix_shared_files_folder_id', 'shared_files', ['folder_id'])
        op.create_index('ix_shared_files_subfolder_id', 'shared_files', ['subfolder_id'])
        op.create_index('ix_shared_files_uploaded_by_id', 'shared_files', ['uploaded_by_id'])
        op.create_index('ix_shared_files_expires_at', 'shared_files', ['expires_at'])

    # Create fileshare_audit_logs table
    if 'fileshare_audit_logs' not in existing_tables:
        op.create_table(
            'fileshare_audit_logs',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('user_email', sa.String(255), nullable=False),
            sa.Column('action', sa.String(50), nullable=False),
            sa.Column('file_id', sa.Integer(), nullable=True),
            sa.Column('filename', sa.String(255), nullable=False),
            sa.Column('folder_slug', sa.String(50), nullable=False),
            sa.Column('subfolder_slug', sa.String(50), nullable=True),
            sa.Column('file_size_bytes', sa.BigInteger(), nullable=True),
            sa.Column('bulk_file_ids', sa.JSON(), nullable=True),
            sa.Column('ip_address', sa.String(45), nullable=True),
            sa.Column('user_agent', sa.String(500), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_fileshare_audit_logs_user_id', 'fileshare_audit_logs', ['user_id'])
        op.create_index('ix_fileshare_audit_logs_action', 'fileshare_audit_logs', ['action'])
        op.create_index('ix_fileshare_audit_logs_created_at', 'fileshare_audit_logs', ['created_at'])


def downgrade() -> None:
    """Remove fileshare tables."""
    # Drop in reverse order due to foreign keys
    op.drop_index('ix_fileshare_audit_logs_created_at', table_name='fileshare_audit_logs')
    op.drop_index('ix_fileshare_audit_logs_action', table_name='fileshare_audit_logs')
    op.drop_index('ix_fileshare_audit_logs_user_id', table_name='fileshare_audit_logs')
    op.drop_table('fileshare_audit_logs')

    op.drop_index('ix_shared_files_expires_at', table_name='shared_files')
    op.drop_index('ix_shared_files_uploaded_by_id', table_name='shared_files')
    op.drop_index('ix_shared_files_subfolder_id', table_name='shared_files')
    op.drop_index('ix_shared_files_folder_id', table_name='shared_files')
    op.drop_table('shared_files')

    op.drop_index('ix_folder_permissions_user_id', table_name='folder_permissions')
    op.drop_index('ix_folder_permissions_folder_id', table_name='folder_permissions')
    op.drop_table('folder_permissions')

    op.drop_index('ix_file_subfolders_folder_id', table_name='file_subfolders')
    op.drop_table('file_subfolders')

    op.drop_index('ix_file_folders_company_id', table_name='file_folders')
    op.drop_table('file_folders')
