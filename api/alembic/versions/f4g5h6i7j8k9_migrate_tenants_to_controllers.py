"""Migrate tenants to controllers

Revision ID: f4g5h6i7j8k9
Revises: e3f4g5h6i7j8
Create Date: 2025-11-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4g5h6i7j8k9'
down_revision: Union[str, None] = 'e3f4g5h6i7j8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Migrate tenants table to controllers table with new structure.
    - Rename table: tenants → controllers
    - Rename columns to clarify purpose
    - Add new fields for controller types (RuckusONE, SmartZone)
    - Migrate existing data: ec_type → controller_subtype
    """
    # Step 1: Create controllers table with new schema
    op.create_table(
        'controllers',
        # Primary identification
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),

        # Controller type hierarchy
        sa.Column('controller_type', sa.String(), nullable=False),  # "RuckusONE" or "SmartZone"
        sa.Column('controller_subtype', sa.String(), nullable=True),  # "MSP" or "EC" (RuckusONE only)

        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),

        # RuckusONE-specific fields (nullable for SmartZone)
        sa.Column('r1_tenant_id', sa.String(), nullable=True),  # R1's tenant identifier
        sa.Column('r1_region', sa.String(), nullable=True),  # "NA", "EU", "APAC"
        sa.Column('encrypted_r1_client_id', sa.String(), nullable=True),
        sa.Column('encrypted_r1_shared_secret', sa.String(), nullable=True),

        # SmartZone-specific fields (nullable for RuckusONE)
        sa.Column('sz_host', sa.String(), nullable=True),
        sa.Column('sz_port', sa.Integer(), nullable=True),
        sa.Column('sz_use_https', sa.Boolean(), nullable=True, default=True),
        sa.Column('encrypted_sz_username', sa.String(), nullable=True),
        sa.Column('encrypted_sz_password', sa.String(), nullable=True),
        sa.Column('sz_version', sa.String(), nullable=True),
    )

    # Create unique constraint
    op.create_unique_constraint(
        'uq_user_controller_name',
        'controllers',
        ['user_id', 'name']
    )

    # Create index on user_id for faster lookups
    op.create_index('ix_controllers_user_id', 'controllers', ['user_id'])
    op.create_index('ix_controllers_controller_type', 'controllers', ['controller_type'])

    # Step 2: Migrate data from tenants to controllers
    # Copy data with field mapping
    op.execute("""
        INSERT INTO controllers (
            id,
            user_id,
            name,
            controller_type,
            controller_subtype,
            created_at,
            updated_at,
            r1_tenant_id,
            r1_region,
            encrypted_r1_client_id,
            encrypted_r1_shared_secret
        )
        SELECT
            id,
            user_id,
            name,
            'RuckusONE' as controller_type,  -- All existing are RuckusONE
            COALESCE(ec_type, 'EC') as controller_subtype,  -- Map ec_type to subtype
            created_at,
            updated_at,
            tenant_id as r1_tenant_id,  -- Rename for clarity
            'NA' as r1_region,  -- Default to NA for existing
            encrypted_client_id as encrypted_r1_client_id,
            encrypted_shared_secret as encrypted_r1_shared_secret
        FROM tenants
    """)

    # Step 3: Update users table foreign keys
    # Drop old foreign keys if they exist (use raw SQL to handle non-existence)
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE users DROP CONSTRAINT IF EXISTS users_active_tenant_id_fkey;
            ALTER TABLE users DROP CONSTRAINT IF EXISTS users_secondary_tenant_id_fkey;
            ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_active_tenant;
            ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_secondary_tenant;
        EXCEPTION
            WHEN undefined_object THEN NULL;
        END $$;
    """))

    # Rename columns in users table
    op.alter_column('users', 'active_tenant_id', new_column_name='active_controller_id')
    op.alter_column('users', 'secondary_tenant_id', new_column_name='secondary_controller_id')

    # Add new foreign keys to controllers table
    op.create_foreign_key(
        'fk_users_active_controller',
        'users', 'controllers',
        ['active_controller_id'], ['id'],
        ondelete='SET NULL'
    )

    op.create_foreign_key(
        'fk_users_secondary_controller',
        'users', 'controllers',
        ['secondary_controller_id'], ['id'],
        ondelete='SET NULL'
    )

    # Step 4: Drop old tenants table
    op.drop_table('tenants')


def downgrade() -> None:
    """
    Rollback: Revert controllers back to tenants table.
    """
    # Step 1: Recreate tenants table with original schema
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=True),
        sa.Column('encrypted_client_id', sa.String(), nullable=True),
        sa.Column('encrypted_shared_secret', sa.String(), nullable=True),
        sa.Column('ec_type', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    op.create_unique_constraint('uq_user_tenant_name', 'tenants', ['user_id', 'name'])

    # Step 2: Copy RuckusONE controllers back to tenants
    op.execute("""
        INSERT INTO tenants (
            id,
            user_id,
            name,
            tenant_id,
            encrypted_client_id,
            encrypted_shared_secret,
            ec_type,
            created_at,
            updated_at
        )
        SELECT
            id,
            user_id,
            name,
            r1_tenant_id as tenant_id,
            encrypted_r1_client_id as encrypted_client_id,
            encrypted_r1_shared_secret as encrypted_shared_secret,
            controller_subtype as ec_type,
            created_at,
            updated_at
        FROM controllers
        WHERE controller_type = 'RuckusONE'
    """)

    # Step 3: Update users table foreign keys back
    # Drop controller foreign keys
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_active_controller;
            ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_secondary_controller;
        EXCEPTION
            WHEN undefined_object THEN NULL;
        END $$;
    """))

    # Rename columns back
    op.alter_column('users', 'active_controller_id', new_column_name='active_tenant_id')
    op.alter_column('users', 'secondary_controller_id', new_column_name='secondary_tenant_id')

    # Recreate foreign keys to tenants
    op.create_foreign_key(
        'users_active_tenant_id_fkey',
        'users', 'tenants',
        ['active_tenant_id'], ['id'],
        ondelete='SET NULL'
    )

    op.create_foreign_key(
        'users_secondary_tenant_id_fkey',
        'users', 'tenants',
        ['secondary_tenant_id'], ['id'],
        ondelete='SET NULL'
    )

    # Step 4: Drop controllers table
    op.drop_index('ix_controllers_controller_type', table_name='controllers')
    op.drop_index('ix_controllers_user_id', table_name='controllers')
    op.drop_constraint('uq_user_controller_name', 'controllers', type_='unique')
    op.drop_table('controllers')
