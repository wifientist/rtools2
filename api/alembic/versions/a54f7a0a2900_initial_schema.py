"""Initial schema

Revision ID: a54f7a0a2900
Revises: 
Create Date: 2025-10-26 02:21:29.911525

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a54f7a0a2900'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create companies table
    op.create_table('companies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('domain', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('domain'),
    sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_companies_id'), 'companies', ['id'], unique=False)

    # Create pending_signup_otps table
    op.create_table('pending_signup_otps',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('otp_code', sa.String(), nullable=False),
    sa.Column('otp_expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_pending_signup_otps_id'), 'pending_signup_otps', ['id'], unique=False)

    # Create users table without tenant foreign keys first
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('role', sa.Enum('admin', 'user', name='roleenum'), nullable=False),
    sa.Column('otp_code', sa.String(), nullable=True),
    sa.Column('otp_expires_at', sa.DateTime(), nullable=True),
    sa.Column('last_authenticated_at', sa.DateTime(), nullable=True),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('active_tenant_id', sa.Integer(), nullable=True),
    sa.Column('secondary_tenant_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)

    # Create tenants table
    op.create_table('tenants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('encrypted_client_id', sa.String(), nullable=False),
    sa.Column('encrypted_shared_secret', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('ec_type', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'name', name='uq_user_tenant_name')
    )
    op.create_index(op.f('ix_tenants_user_id'), 'tenants', ['user_id'], unique=False)

    # Now add foreign keys from users to tenants
    op.create_foreign_key('fk_users_active_tenant', 'users', 'tenants', ['active_tenant_id'], ['id'])
    op.create_foreign_key('fk_users_secondary_tenant', 'users', 'tenants', ['secondary_tenant_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop foreign keys from users to tenants first
    op.drop_constraint('fk_users_secondary_tenant', 'users', type_='foreignkey')
    op.drop_constraint('fk_users_active_tenant', 'users', type_='foreignkey')

    # Drop tables in reverse order
    op.drop_index(op.f('ix_tenants_user_id'), table_name='tenants')
    op.drop_table('tenants')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_pending_signup_otps_id'), table_name='pending_signup_otps')
    op.drop_table('pending_signup_otps')
    op.drop_index(op.f('ix_companies_id'), table_name='companies')
    op.drop_table('companies')
