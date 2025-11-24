# Models package - import all models for Alembic autogenerate
from models.user import User, RoleEnum
from models.company import Company
from models.controller import Controller  # NEW: Replaces Tenant
# from models.tenant import Tenant  # DEPRECATED: Migrated to Controller
from models.pending_signup import PendingSignupOtp
from models.revoked_token import RevokedToken
from models.audit_log import AuditLog

__all__ = ['User', 'RoleEnum', 'Company', 'Controller', 'PendingSignupOtp', 'RevokedToken', 'AuditLog']
