"""
DFS Blacklist Router — alpha-gated.

CRUD for DFS blacklist configurations, manual trigger, event history,
blacklist management, and audit trail.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from dependencies import get_db, get_current_user
from decorators import require_alpha
from models.user import User
from models.dfs_blacklist import (
    DfsBlacklistConfig, DfsEvent, DfsBlacklistEntry, DfsAuditLog,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dfs-blacklist", tags=["DFS Blacklist"])


# ── Schemas ──────────────────────────────────────────────────


class ZoneItem(BaseModel):
    id: str
    name: str


class ApGroupItem(BaseModel):
    id: str
    name: str


class ThresholdItem(BaseModel):
    count: int
    backoff_hours: int


class ThresholdsSchema(BaseModel):
    hourly: Optional[ThresholdItem] = None
    daily: Optional[ThresholdItem] = None
    weekly: Optional[ThresholdItem] = None


class ConfigCreate(BaseModel):
    controller_id: int
    zones: list[ZoneItem]
    ap_groups: list[ApGroupItem] = []
    thresholds: ThresholdsSchema
    event_filters: Optional[list[dict]] = None
    slack_webhook_url: Optional[str] = None
    enabled: bool = True


class ConfigUpdate(BaseModel):
    zones: Optional[list[ZoneItem]] = None
    ap_groups: Optional[list[ApGroupItem]] = None
    thresholds: Optional[ThresholdsSchema] = None
    event_filters: Optional[list[dict]] = None
    slack_webhook_url: Optional[str] = None
    enabled: Optional[bool] = None


# ── Helpers ──────────────────────────────────────────────────


def _get_config_or_404(config_id: int, db: Session, current_user: User) -> DfsBlacklistConfig:
    config = db.query(DfsBlacklistConfig).filter(DfsBlacklistConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="DFS blacklist config not found")
    if config.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied to this DFS blacklist config")
    return config


def _config_to_dict(config: DfsBlacklistConfig) -> dict:
    controller = config.controller
    return {
        "id": config.id,
        "controller_id": config.controller_id,
        "controller_name": (
            controller.name if controller else "Unknown"
        ),
        "zones": config.zones or [],
        "ap_groups": config.ap_groups or [],
        "thresholds": config.thresholds or {},
        "event_filters": config.event_filters,
        "slack_configured": config.slack_webhook_url is not None and config.slack_webhook_url != "",
        "enabled": config.enabled,
        "owner_id": config.owner_id,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


def _event_to_dict(event: DfsEvent) -> dict:
    return {
        "id": event.id,
        "sz_event_id": event.sz_event_id,
        "event_code": event.event_code,
        "event_type": event.event_type,
        "category": event.category,
        "severity": event.severity,
        "activity": event.activity,
        "channel": event.channel,
        "zone_id": event.zone_id,
        "zone_name": event.zone_name,
        "ap_group_id": event.ap_group_id,
        "ap_group_name": event.ap_group_name,
        "ap_mac": event.ap_mac,
        "ap_name": event.ap_name,
        "event_timestamp": event.event_timestamp.isoformat() if event.event_timestamp else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _entry_to_dict(entry: DfsBlacklistEntry) -> dict:
    return {
        "id": entry.id,
        "channel": entry.channel,
        "zone_id": entry.zone_id,
        "zone_name": entry.zone_name,
        "ap_group_id": entry.ap_group_id,
        "ap_group_name": entry.ap_group_name,
        "threshold_type": entry.threshold_type,
        "event_count": entry.event_count,
        "blacklisted_at": entry.blacklisted_at.isoformat() if entry.blacklisted_at else None,
        "reentry_at": entry.reentry_at.isoformat() if entry.reentry_at else None,
        "reentry_completed_at": entry.reentry_completed_at.isoformat() if entry.reentry_completed_at else None,
        "status": entry.status,
    }


def _audit_to_dict(audit: DfsAuditLog) -> dict:
    return {
        "id": audit.id,
        "action": audit.action,
        "details": audit.details,
        "created_at": audit.created_at.isoformat() if audit.created_at else None,
    }


# ── Config CRUD ──────────────────────────────────────────────


@router.get("/configs")
@require_alpha()
async def list_configs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all DFS blacklist configs for the current user."""
    configs = (
        db.query(DfsBlacklistConfig)
        .filter(DfsBlacklistConfig.owner_id == current_user.id)
        .order_by(DfsBlacklistConfig.created_at.desc())
        .all()
    )
    return [_config_to_dict(c) for c in configs]


@router.post("/configs", status_code=201)
@require_alpha()
async def create_config(
    body: ConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new DFS blacklist config."""
    from clients.sz_client_deps import validate_controller_access
    validate_controller_access(body.controller_id, current_user, db)

    config = DfsBlacklistConfig(
        controller_id=body.controller_id,
        owner_id=current_user.id,
        zones=[z.model_dump() for z in body.zones],
        ap_groups=[g.model_dump() for g in body.ap_groups],
        thresholds=body.thresholds.model_dump(exclude_none=True),
        event_filters=body.event_filters,
        slack_webhook_url=body.slack_webhook_url,
        enabled=body.enabled,
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    audit = DfsAuditLog(
        config_id=config.id,
        action="config_created",
        details={"controller_id": body.controller_id, "zones": len(body.zones)},
    )
    db.add(audit)
    db.commit()

    logger.info("Created DFS blacklist config %d for user %d", config.id, current_user.id)
    return _config_to_dict(config)


@router.get("/configs/{config_id}")
@require_alpha()
async def get_config(
    config_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single DFS blacklist config."""
    config = _get_config_or_404(config_id, db, current_user)
    return _config_to_dict(config)


@router.put("/configs/{config_id}")
@require_alpha()
async def update_config(
    config_id: int,
    body: ConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a DFS blacklist config."""
    config = _get_config_or_404(config_id, db, current_user)

    changes = {}
    if body.zones is not None:
        config.zones = [z.model_dump() for z in body.zones]
        changes["zones"] = len(body.zones)
    if body.ap_groups is not None:
        config.ap_groups = [g.model_dump() for g in body.ap_groups]
        changes["ap_groups"] = len(body.ap_groups)
    if body.thresholds is not None:
        config.thresholds = body.thresholds.model_dump(exclude_none=True)
        changes["thresholds"] = "updated"
    if body.event_filters is not None:
        config.event_filters = body.event_filters
        changes["event_filters"] = "updated"
    if body.slack_webhook_url is not None:
        config.slack_webhook_url = body.slack_webhook_url
        changes["slack_webhook_url"] = "updated"
    if body.enabled is not None:
        config.enabled = body.enabled
        changes["enabled"] = body.enabled

    db.commit()
    db.refresh(config)

    audit = DfsAuditLog(
        config_id=config.id,
        action="config_updated",
        details=changes,
    )
    db.add(audit)
    db.commit()

    return _config_to_dict(config)


@router.delete("/configs/{config_id}", status_code=204)
@require_alpha()
async def delete_config(
    config_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a DFS blacklist config and all related data."""
    config = _get_config_or_404(config_id, db, current_user)
    db.delete(config)
    db.commit()
    logger.info("Deleted DFS blacklist config %d", config_id)


# ── Manual Trigger ───────────────────────────────────────────


@router.post("/configs/{config_id}/trigger")
@require_alpha()
async def trigger_config(
    config_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually trigger a DFS blacklist check for a specific config."""
    config = _get_config_or_404(config_id, db, current_user)

    from jobs.dfs_blacklist_job import run_single_config
    result = await run_single_config(config_id)
    return result


# ── Events ───────────────────────────────────────────────────


@router.get("/configs/{config_id}/events")
@require_alpha()
async def list_events(
    config_id: int,
    channel: Optional[int] = None,
    zone_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List DFS events for a config with optional filters."""
    _get_config_or_404(config_id, db, current_user)

    query = (
        db.query(DfsEvent)
        .filter(DfsEvent.config_id == config_id)
    )

    if channel is not None:
        query = query.filter(DfsEvent.channel == channel)
    if zone_id:
        query = query.filter(DfsEvent.zone_id == zone_id)
    if start_date:
        query = query.filter(DfsEvent.event_timestamp >= datetime.fromisoformat(start_date))
    if end_date:
        query = query.filter(DfsEvent.event_timestamp <= datetime.fromisoformat(end_date))

    total = query.count()
    events = (
        query.order_by(DfsEvent.event_timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "events": [_event_to_dict(e) for e in events],
    }


# ── Blacklist Entries ────────────────────────────────────────


@router.get("/configs/{config_id}/blacklist")
@require_alpha()
async def list_blacklist_entries(
    config_id: int,
    status_filter: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List blacklist entries for a config."""
    _get_config_or_404(config_id, db, current_user)

    query = (
        db.query(DfsBlacklistEntry)
        .filter(DfsBlacklistEntry.config_id == config_id)
    )

    if status_filter:
        query = query.filter(DfsBlacklistEntry.status == status_filter)

    total = query.count()
    entries = (
        query.order_by(DfsBlacklistEntry.blacklisted_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "entries": [_entry_to_dict(e) for e in entries],
    }


@router.delete("/configs/{config_id}/blacklist/{entry_id}")
@require_alpha()
async def remove_blacklist_entry(
    config_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually remove a blacklist entry."""
    _get_config_or_404(config_id, db, current_user)

    entry = (
        db.query(DfsBlacklistEntry)
        .filter(
            DfsBlacklistEntry.id == entry_id,
            DfsBlacklistEntry.config_id == config_id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Blacklist entry not found")

    entry.status = "manually_removed"
    entry.reentry_completed_at = datetime.utcnow()

    audit = DfsAuditLog(
        config_id=config_id,
        action="channel_manually_removed",
        details={
            "channel": entry.channel,
            "removed_by": current_user.id,
            "was_threshold_type": entry.threshold_type,
        },
    )
    db.add(audit)
    db.commit()

    return {"status": "removed", "channel": entry.channel}


# ── Audit Trail ──────────────────────────────────────────────


@router.get("/configs/{config_id}/audit")
@require_alpha()
async def list_audit_logs(
    config_id: int,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get audit trail for a DFS blacklist config."""
    _get_config_or_404(config_id, db, current_user)

    query = (
        db.query(DfsAuditLog)
        .filter(DfsAuditLog.config_id == config_id)
    )

    total = query.count()
    logs = (
        query.order_by(DfsAuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "logs": [_audit_to_dict(a) for a in logs],
    }


# ── Dashboard ────────────────────────────────────────────────


@router.get("/configs/{config_id}/dashboard")
@require_alpha()
async def get_dashboard(
    config_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregated dashboard data for a DFS blacklist config."""
    config = _get_config_or_404(config_id, db, current_user)
    now = datetime.utcnow()

    # Active blacklist entries
    active_entries = (
        db.query(DfsBlacklistEntry)
        .filter(
            DfsBlacklistEntry.config_id == config_id,
            DfsBlacklistEntry.status == "active",
        )
        .all()
    )

    # Event counts per channel in each window
    from sqlalchemy import func
    from datetime import timedelta

    windows = {
        "hourly": now - timedelta(hours=1),
        "daily": now - timedelta(hours=24),
        "weekly": now - timedelta(days=7),
    }

    channel_stats = {}
    for window_name, window_start in windows.items():
        rows = (
            db.query(DfsEvent.channel, func.count(DfsEvent.id))
            .filter(
                DfsEvent.config_id == config_id,
                DfsEvent.channel.isnot(None),
                DfsEvent.event_timestamp >= window_start,
            )
            .group_by(DfsEvent.channel)
            .all()
        )
        for ch, count in rows:
            channel_stats.setdefault(ch, {})[window_name] = count

    # Recent audit entries
    recent_audit = (
        db.query(DfsAuditLog)
        .filter(DfsAuditLog.config_id == config_id)
        .order_by(DfsAuditLog.created_at.desc())
        .limit(10)
        .all()
    )

    return {
        "config": _config_to_dict(config),
        "active_blacklist": [_entry_to_dict(e) for e in active_entries],
        "channel_stats": channel_stats,
        "thresholds": config.thresholds or {},
        "recent_audit": [_audit_to_dict(a) for a in recent_audit],
    }


# ── Slack Test ───────────────────────────────────────────────


@router.post("/test-slack")
@require_alpha()
async def test_slack_webhook(
    webhook_url: str,
    current_user: User = Depends(get_current_user),
):
    """Send a test message to a Slack webhook to verify it works."""
    from utils.slack import send_slack_message

    success = await send_slack_message(
        webhook_url,
        text="DFS Blacklist test notification from ruckus.tools",
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to send Slack message. Check webhook URL.")
    return {"status": "ok", "message": "Test message sent successfully"}
