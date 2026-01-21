"""
DPSK Orchestrator database models.

These models support the DPSK Orchestrator feature which automatically syncs
passphrases from per-unit DPSK pools to a site-wide DPSK pool.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, JSON, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class DPSKOrchestrator(Base):
    """
    Configuration for a DPSK orchestrator instance.

    An orchestrator manages the sync between multiple per-unit DPSK pools
    (source pools) and a single site-wide DPSK pool (target pool).
    """
    __tablename__ = "dpsk_orchestrators"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)              # e.g., "Parkview Apartments"

    # Controller association
    controller_id = Column(Integer, ForeignKey("controllers.id"), nullable=False)
    tenant_id = Column(String, nullable=True)          # For MSP multi-tenancy
    venue_id = Column(String, nullable=True)           # Scope to specific venue

    # Site-wide pool target
    site_wide_pool_id = Column(String, nullable=False) # Target DPSK pool ID
    site_wide_pool_name = Column(String, nullable=True)  # For display
    site_wide_identity_group_id = Column(String, nullable=True)  # Associated identity group

    # Configuration
    sync_interval_minutes = Column(Integer, default=30)
    enabled = Column(Boolean, default=True)
    auto_delete = Column(Boolean, default=False)       # False = flag for manual review

    # Auto-discovery configuration
    auto_discover_enabled = Column(Boolean, default=True)
    include_patterns = Column(JSON, default=["Unit*", "*PerUnit*"])  # Glob patterns to include
    exclude_patterns = Column(JSON, default=["SiteWide*", "Guest*", "Visitor*"])  # Glob patterns to exclude

    # Webhook configuration (for RuckusONE webhooks)
    webhook_id = Column(String, nullable=True)         # RuckusONE webhook ID
    webhook_secret = Column(String, nullable=True)     # For signature verification

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    last_sync_at = Column(DateTime, nullable=True)
    last_discovery_at = Column(DateTime, nullable=True)

    # Statistics
    discovered_pools_count = Column(Integer, default=0)

    # Relationships
    controller = relationship("Controller")
    source_pools = relationship("OrchestratorSourcePool", back_populates="orchestrator", cascade="all, delete-orphan")
    sync_events = relationship("OrchestratorSyncEvent", back_populates="orchestrator", cascade="all, delete-orphan")
    passphrase_mappings = relationship("PassphraseMapping", back_populates="orchestrator", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<DPSKOrchestrator id={self.id} name='{self.name}' enabled={self.enabled}>"


class OrchestratorSourcePool(Base):
    """
    Per-unit DPSK pools that feed into the site-wide pool.

    Each source pool represents a per-unit DPSK pool whose passphrases
    should be synced to the site-wide pool.
    """
    __tablename__ = "orchestrator_source_pools"

    id = Column(Integer, primary_key=True)
    orchestrator_id = Column(Integer, ForeignKey("dpsk_orchestrators.id", ondelete="CASCADE"), nullable=False)

    # Pool identifiers
    pool_id = Column(String, nullable=False)           # RuckusONE DPSK pool ID
    pool_name = Column(String, nullable=True)          # e.g., "Unit101DPSK"
    identity_group_id = Column(String, nullable=True)  # Associated identity group

    # Tracking
    last_sync_at = Column(DateTime, nullable=True)
    passphrase_count = Column(Integer, default=0)

    # Discovery metadata
    discovered_at = Column(DateTime, nullable=True)    # When auto-discovered (null if manually added)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    orchestrator = relationship("DPSKOrchestrator", back_populates="source_pools")

    def __repr__(self):
        return f"<OrchestratorSourcePool id={self.id} pool_name='{self.pool_name}'>"


class OrchestratorSyncEvent(Base):
    """
    Audit log of sync operations.

    Each sync operation (webhook-triggered, scheduled, or manual) creates
    a record to track what happened and any issues.
    """
    __tablename__ = "orchestrator_sync_events"

    id = Column(Integer, primary_key=True)
    orchestrator_id = Column(Integer, ForeignKey("dpsk_orchestrators.id", ondelete="CASCADE"), nullable=False)

    # Event type
    event_type = Column(String, nullable=False)        # "webhook", "scheduled", "manual"
    trigger_activity_id = Column(String, nullable=True)  # RuckusONE activity ID (for webhook)
    source_pool_id = Column(String, nullable=True)     # If triggered for specific pool

    # Results
    status = Column(String, nullable=False, default="running")  # "running", "success", "partial", "failed"
    added_count = Column(Integer, default=0)
    updated_count = Column(Integer, default=0)
    flagged_for_removal = Column(Integer, default=0)
    orphans_found = Column(Integer, default=0)
    errors = Column(JSON, default=list)

    # Discovery results (if auto-discovery ran)
    pools_scanned = Column(Integer, default=0)
    pools_discovered = Column(Integer, default=0)

    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    orchestrator = relationship("DPSKOrchestrator", back_populates="sync_events")

    def __repr__(self):
        return f"<OrchestratorSyncEvent id={self.id} type={self.event_type} status={self.status}>"


class PassphraseMapping(Base):
    """
    Tracks which source passphrases map to which site-wide passphrases.

    This enables efficient incremental sync and supports the audit trail
    for understanding where each site-wide passphrase came from.
    """
    __tablename__ = "passphrase_mappings"

    id = Column(Integer, primary_key=True)
    orchestrator_id = Column(Integer, ForeignKey("dpsk_orchestrators.id", ondelete="CASCADE"), nullable=False)

    # Source (per-unit pool)
    source_pool_id = Column(String, nullable=False)
    source_pool_name = Column(String, nullable=True)   # For display
    source_passphrase_id = Column(String, nullable=True)  # Null for orphans
    source_username = Column(String, nullable=True)

    # Source identity (if tracked)
    source_identity_id = Column(String, nullable=True)

    # Target (site-wide pool)
    target_passphrase_id = Column(String, nullable=True)  # Null if not yet synced
    target_identity_id = Column(String, nullable=True)

    # Sync status
    # - "pending": Not yet synced
    # - "synced": Successfully synced
    # - "flagged_removal": Source deleted, awaiting manual resolution
    # - "orphan": Exists in site-wide but not in any source pool
    # - "ignored": Manually dismissed
    # - "target_missing": We had a mapping but target passphrase no longer exists in site-wide
    #                    (different from flagged_removal - the target was deleted externally or via UI)
    sync_status = Column(String, default="pending", nullable=False)

    # For orphans: suggested target pool based on VLAN match
    suggested_source_pool_id = Column(String, nullable=True)

    # Passphrase details (cached for display)
    vlan_id = Column(Integer, nullable=True)           # Preserved VLAN
    passphrase_preview = Column(String, nullable=True) # First few chars for identification

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    last_synced_at = Column(DateTime, nullable=True)
    flagged_at = Column(DateTime, nullable=True)       # When marked for removal

    # Relationships
    orchestrator = relationship("DPSKOrchestrator", back_populates="passphrase_mappings")

    def __repr__(self):
        return f"<PassphraseMapping id={self.id} username='{self.source_username}' status={self.sync_status}>"
