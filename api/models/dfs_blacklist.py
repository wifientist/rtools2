"""
DFS Blacklist models.

Tracks DFS radar events per channel/zone, manages channel blacklisting
with configurable thresholds and backoff timers.
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, JSON, ForeignKey, Index,
)
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base
from utils.encryption import encrypt_value, decrypt_value


class DfsBlacklistConfig(Base):
    """User-defined configuration for DFS channel monitoring on a SmartZone controller."""
    __tablename__ = "dfs_blacklist_configs"

    id = Column(Integer, primary_key=True, index=True)
    controller_id = Column(
        Integer,
        ForeignKey("controllers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Scope: which zones and AP groups to monitor
    zones = Column(JSON, nullable=False, default=list)           # [{id, name}, ...]
    ap_groups = Column(JSON, nullable=False, default=list)       # [{id, name}, ...]

    # Thresholds: when to blacklist a channel
    # Structure: {
    #   "hourly":  {"count": 3, "backoff_hours": 6},
    #   "daily":   {"count": 8, "backoff_hours": 48},
    #   "weekly":  {"count": 15, "backoff_hours": 168}
    # }
    thresholds = Column(JSON, nullable=False)

    # Flexible SZ query filters — passed through to extraFilters on the event API
    event_filters = Column(JSON, nullable=True)

    # Notification (encrypted at rest)
    encrypted_slack_webhook_url = Column(String(500), nullable=True)

    enabled = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    owner = relationship("User", foreign_keys=[owner_id])
    controller = relationship("Controller")
    events = relationship("DfsEvent", back_populates="config", cascade="all, delete-orphan")
    blacklist_entries = relationship("DfsBlacklistEntry", back_populates="config", cascade="all, delete-orphan")
    audit_logs = relationship("DfsAuditLog", back_populates="config", cascade="all, delete-orphan")

    @property
    def slack_webhook_url(self) -> str | None:
        if self.encrypted_slack_webhook_url:
            return decrypt_value(self.encrypted_slack_webhook_url)
        return None

    @slack_webhook_url.setter
    def slack_webhook_url(self, value: str | None):
        if value:
            self.encrypted_slack_webhook_url = encrypt_value(value)
        else:
            self.encrypted_slack_webhook_url = None

    def __repr__(self):
        return f"<DfsBlacklistConfig id={self.id} controller={self.controller_id} enabled={self.enabled}>"


class DfsEvent(Base):
    """Raw DFS event pulled from a SmartZone controller."""
    __tablename__ = "dfs_events"
    __table_args__ = (
        Index("ix_dfs_events_config_timestamp", "config_id", "event_timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    config_id = Column(
        Integer,
        ForeignKey("dfs_blacklist_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SZ event fields
    sz_event_id = Column(String, nullable=True, index=True)  # Dedup key from SZ
    event_code = Column(Integer, nullable=True)
    event_type = Column(String, nullable=True)
    category = Column(String, nullable=True)
    severity = Column(String, nullable=True)
    activity = Column(Text, nullable=True)

    # Parsed DFS-specific fields
    channel = Column(Integer, nullable=True)
    zone_id = Column(String, nullable=True)
    zone_name = Column(String, nullable=True)
    ap_group_id = Column(String, nullable=True)
    ap_group_name = Column(String, nullable=True)
    ap_mac = Column(String, nullable=True)
    ap_name = Column(String, nullable=True)

    # Timestamps
    event_timestamp = Column(DateTime, nullable=True)  # insertionTime from SZ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Full raw event for future parsing improvements
    raw_data = Column(JSON, nullable=True)

    config = relationship("DfsBlacklistConfig", back_populates="events")

    def __repr__(self):
        return f"<DfsEvent id={self.id} code={self.event_code} ch={self.channel}>"


class DfsBlacklistEntry(Base):
    """A channel that has been blacklisted due to exceeding DFS event thresholds."""
    __tablename__ = "dfs_blacklist_entries"

    id = Column(Integer, primary_key=True, index=True)
    config_id = Column(
        Integer,
        ForeignKey("dfs_blacklist_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    channel = Column(Integer, nullable=False)
    zone_id = Column(String, nullable=True)
    zone_name = Column(String, nullable=True)
    ap_group_id = Column(String, nullable=True)
    ap_group_name = Column(String, nullable=True)

    # Which threshold triggered the blacklist
    threshold_type = Column(String(10), nullable=False)  # hourly, daily, weekly
    event_count = Column(Integer, nullable=False)

    # Timing
    blacklisted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reentry_at = Column(DateTime, nullable=False)  # When the backoff expires
    reentry_completed_at = Column(DateTime, nullable=True)  # When actually re-enabled

    # Status: active, expired, manually_removed
    status = Column(String(20), nullable=False, default="active", index=True)

    config = relationship("DfsBlacklistConfig", back_populates="blacklist_entries")

    def __repr__(self):
        return f"<DfsBlacklistEntry id={self.id} ch={self.channel} status={self.status}>"


class DfsAuditLog(Base):
    """Audit trail for DFS blacklist actions and decisions."""
    __tablename__ = "dfs_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    config_id = Column(
        Integer,
        ForeignKey("dfs_blacklist_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Action types: config_created, config_updated, events_collected,
    # channel_blacklisted, channel_expired, channel_manually_removed, job_run
    action = Column(String(50), nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    config = relationship("DfsBlacklistConfig", back_populates="audit_logs")

    def __repr__(self):
        return f"<DfsAuditLog id={self.id} action={self.action}>"
