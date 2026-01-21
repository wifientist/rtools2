# Models package - import all models for Alembic autogenerate
from models.user import User, RoleEnum
from models.company import Company
from models.controller import Controller  # NEW: Replaces Tenant
# from models.tenant import Tenant  # DEPRECATED: Migrated to Controller
from models.pending_signup import PendingSignupOtp
from models.revoked_token import RevokedToken
from models.audit_log import AuditLog

# Scheduler models
from models.scheduler import ScheduledJob, ScheduledJobRun

# DPSK Orchestrator models
from models.orchestrator import (
    DPSKOrchestrator,
    OrchestratorSourcePool,
    OrchestratorSyncEvent,
    PassphraseMapping
)

__all__ = [
    'User', 'RoleEnum', 'Company', 'Controller',
    'PendingSignupOtp', 'RevokedToken', 'AuditLog',
    # Scheduler
    'ScheduledJob', 'ScheduledJobRun',
    # Orchestrator
    'DPSKOrchestrator', 'OrchestratorSourcePool',
    'OrchestratorSyncEvent', 'PassphraseMapping'
]
