# Models package - import all models for Alembic autogenerate
from models.user import User, RoleEnum
from models.company import Company
from models.controller import Controller  # NEW: Replaces Tenant
# from models.tenant import Tenant  # DEPRECATED: Migrated to Controller
from models.pending_signup import PendingSignupOtp
from models.revoked_token import RevokedToken
from models.audit_log import AuditLog
from models.signup_attempt import SignupAttempt

# Scheduler models
from models.scheduler import ScheduledJob, ScheduledJobRun

# DPSK Orchestrator models
from models.orchestrator import (
    DPSKOrchestrator,
    OrchestratorSourcePool,
    OrchestratorSyncEvent,
    PassphraseMapping
)

# Fileshare models
from models.fileshare import (
    FileFolder,
    FileSubfolder,
    FolderPermission,
    SharedFile,
    FileshareAuditLog,
    PermissionType
)

# Migration Dashboard
from models.migration_dashboard_settings import MigrationDashboardSettings
from models.migration_dashboard_snapshot import MigrationDashboardSnapshot

# SZ Config Migration
from models.sz_migration_session import SZMigrationSession

__all__ = [
    'User', 'RoleEnum', 'Company', 'Controller',
    'PendingSignupOtp', 'RevokedToken', 'AuditLog', 'SignupAttempt',
    # Scheduler
    'ScheduledJob', 'ScheduledJobRun',
    # Orchestrator
    'DPSKOrchestrator', 'OrchestratorSourcePool',
    'OrchestratorSyncEvent', 'PassphraseMapping',
    # Fileshare
    'FileFolder', 'FileSubfolder', 'FolderPermission',
    'SharedFile', 'FileshareAuditLog', 'PermissionType',
    # Migration Dashboard
    'MigrationDashboardSettings', 'MigrationDashboardSnapshot',
    # SZ Config Migration
    'SZMigrationSession',
]
