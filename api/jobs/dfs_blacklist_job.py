"""
Scheduled job: DFS Blacklist channel monitoring.

Runs hourly. For each enabled DfsBlacklistConfig, queries the SmartZone
event API for DFS-related events, updates rolling counters per channel,
evaluates thresholds, and manages blacklist entries with backoff timers.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from database import SessionLocal
from models.dfs_blacklist import (
    DfsBlacklistConfig, DfsEvent, DfsBlacklistEntry, DfsAuditLog,
)
from clients.sz_client_deps import create_sz_client_from_controller

logger = logging.getLogger(__name__)

JOB_ID = "dfs_blacklist"
TRIGGER_CONFIG = {"minutes": 60}

# Default DFS event codes to track
DFS_EVENT_CODES = {306}

# Rolling window definitions
WINDOWS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(hours=24),
    "weekly": timedelta(days=7),
}


def _parse_channel_from_activity(activity: str) -> Optional[int]:
    """
    Attempt to extract the channel number from an SZ event activity string.

    Common patterns:
      - "... switches to channel 100 ..."
      - "... on channel 52 ..."
      - "... Channel=149 ..."
    """
    if not activity:
        return None
    # Try various patterns
    patterns = [
        r"channel\s*[=:]?\s*(\d+)",
        r"Channel\s*[=:]?\s*(\d+)",
        r"ch\s*[=:]?\s*(\d+)",
    ]
    for pat in patterns:
        match = re.search(pat, activity, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _parse_ap_info_from_activity(activity: str) -> Dict[str, Optional[str]]:
    """Extract AP MAC/name from activity string if present."""
    info: Dict[str, Optional[str]] = {"ap_mac": None, "ap_name": None}
    if not activity:
        return info
    # Common pattern: "AP[xx:xx:xx:xx:xx:xx, APName]"
    mac_match = re.search(r"AP\[([0-9A-Fa-f:]{17})", activity)
    if mac_match:
        info["ap_mac"] = mac_match.group(1)
    name_match = re.search(r"AP\[[0-9A-Fa-f:]{17},\s*([^\]]+)\]", activity)
    if name_match:
        info["ap_name"] = name_match.group(1).strip()
    return info


async def run_dfs_blacklist() -> Dict[str, Any]:
    """
    Main scheduled job entry point.

    Processes all enabled DFS blacklist configs.
    """
    # Load configs, then release the session — _process_config manages its own
    db = SessionLocal()
    try:
        configs = (
            db.query(DfsBlacklistConfig)
            .filter(DfsBlacklistConfig.enabled == True)
            .all()
        )
        if not configs:
            logger.info("No enabled DFS blacklist configs found")
            return {"status": "ok", "configs_processed": 0}
        # Collect config IDs so we can re-query inside _process_config
        config_ids = [c.id for c in configs]
    finally:
        db.close()

    results = []
    for config_id in config_ids:
        try:
            result = await _process_single(config_id)
            results.append(result)
        except Exception as e:
            logger.error(
                "Error processing DFS config %d: %s", config_id, e, exc_info=True
            )
            results.append({
                "config_id": config_id,
                "status": "error",
                "error": str(e),
            })

    return {
        "status": "ok",
        "configs_processed": len(config_ids),
        "results": results,
    }


async def run_single_config(config_id: int) -> Dict[str, Any]:
    """Run the DFS check for a single config (manual trigger)."""
    return await _process_single(config_id)


async def _process_single(config_id: int) -> Dict[str, Any]:
    """Load a config by ID in its own session and process it."""
    db = SessionLocal()
    try:
        config = (
            db.query(DfsBlacklistConfig)
            .filter(DfsBlacklistConfig.id == config_id)
            .first()
        )
        if not config:
            return {"status": "error", "error": "Config not found"}
        # _process_config will close this db and open a new one after network I/O
        return await _process_config(db, config)
    except Exception:
        db.close()
        raise


async def _process_config(db, config: DfsBlacklistConfig) -> Dict[str, Any]:
    """
    Process a single DFS blacklist config:
    1. Query SZ for DFS events (DB session released during network I/O)
    2. Store new events
    3. Evaluate thresholds per channel
    4. Create/update blacklist entries
    5. Sweep expired blacklists
    6. Send notifications
    """
    now = datetime.utcnow()

    # --- Build SZ client while we still have the session, then release it ---
    sz_client = create_sz_client_from_controller(config.controller_id, db)
    controller_name = sz_client.host

    # Collect config data we need before releasing the session
    config_id = config.id
    zones = config.zones or []
    ap_groups = config.ap_groups or []
    event_filters = config.event_filters

    zone_ids = [z["id"] for z in zones if "id" in z]
    ap_group_ids = [g["id"] for g in ap_groups if "id" in g]

    if not zone_ids:
        logger.warning("Config %d has no zones configured, skipping", config_id)
        return {"config_id": config_id, "status": "skipped", "reason": "no zones"}

    # Calculate time window — look back 1 week to have full data for all windows
    lookback = timedelta(days=7, hours=1)  # Extra hour buffer for overlap
    start_ms = int((now - lookback).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    # Release DB connection during network-bound SZ query
    db.close()

    # Query SZ events (no DB connection held)
    new_event_count = 0
    async with sz_client:
        raw_events = await sz_client.events.query_dfs_events(
            zone_ids=zone_ids,
            ap_group_ids=ap_group_ids if ap_group_ids else None,
            start_epoch_ms=start_ms,
            end_epoch_ms=end_ms,
            additional_filters=event_filters,
        )

    # Re-acquire DB session for storing results
    db = SessionLocal()
    try:
        # Re-load the config in the new session
        config = db.query(DfsBlacklistConfig).filter(DfsBlacklistConfig.id == config_id).first()

        # Store new events (deduplicate by sz_event_id)
        existing_sz_ids = set()
        if raw_events:
            sz_ids_in_batch = [e.get("id") for e in raw_events if e.get("id")]
            if sz_ids_in_batch:
                existing = (
                    db.query(DfsEvent.sz_event_id)
                    .filter(
                        DfsEvent.config_id == config_id,
                        DfsEvent.sz_event_id.in_(sz_ids_in_batch),
                    )
                    .all()
                )
                existing_sz_ids = {row[0] for row in existing}

        for raw in raw_events:
            sz_id = raw.get("id")
            if sz_id and sz_id in existing_sz_ids:
                continue

            activity = raw.get("activity", "")
            channel = _parse_channel_from_activity(activity)
            ap_info = _parse_ap_info_from_activity(activity)

            # Parse insertion time (epoch ms)
            insertion_ms = raw.get("insertionTime")
            event_ts = (
                datetime.utcfromtimestamp(insertion_ms / 1000)
                if insertion_ms
                else now
            )

            event = DfsEvent(
                config_id=config_id,
                sz_event_id=sz_id,
                event_code=raw.get("eventCode"),
                event_type=raw.get("eventType"),
                category=raw.get("category"),
                severity=raw.get("severity"),
                activity=activity,
                channel=channel,
                ap_mac=ap_info["ap_mac"],
                ap_name=ap_info["ap_name"],
                event_timestamp=event_ts,
                raw_data=raw,
            )
            db.add(event)
            new_event_count += 1

        if new_event_count > 0:
            db.commit()
            logger.info(
                "Config %d: stored %d new DFS events", config_id, new_event_count
            )

        # Evaluate thresholds per channel
        blacklisted_channels = await _evaluate_thresholds(db, config, now, controller_name)

        # Sweep expired blacklist entries
        expired_count = await _sweep_expired(db, config, now, controller_name)

        # Audit log the run
        audit = DfsAuditLog(
            config_id=config_id,
            action="job_run",
            details={
                "new_events": new_event_count,
                "total_raw_events": len(raw_events),
                "channels_blacklisted": len(blacklisted_channels),
                "channels_expired": expired_count,
            },
        )
        db.add(audit)
        db.commit()

        return {
            "config_id": config_id,
            "status": "ok",
            "new_events": new_event_count,
            "channels_blacklisted": blacklisted_channels,
            "channels_expired": expired_count,
        }
    finally:
        db.close()


async def _evaluate_thresholds(
    db, config: DfsBlacklistConfig, now: datetime, controller_name: str
) -> list[int]:
    """
    Count events per channel within each window and blacklist if threshold exceeded.

    Returns list of newly blacklisted channels.
    """
    thresholds = config.thresholds or {}
    newly_blacklisted: list[int] = []

    # Get all events with a channel for this config within the weekly window
    week_ago = now - WINDOWS["weekly"]
    events = (
        db.query(DfsEvent)
        .filter(
            DfsEvent.config_id == config.id,
            DfsEvent.channel.isnot(None),
            DfsEvent.event_timestamp >= week_ago,
        )
        .all()
    )

    # Group events by channel
    channel_events: Dict[int, list[DfsEvent]] = {}
    for evt in events:
        channel_events.setdefault(evt.channel, []).append(evt)

    # Get currently active blacklist entries to avoid re-blacklisting
    active_entries = (
        db.query(DfsBlacklistEntry)
        .filter(
            DfsBlacklistEntry.config_id == config.id,
            DfsBlacklistEntry.status == "active",
        )
        .all()
    )
    active_channels = {(e.channel, e.zone_id) for e in active_entries}

    for channel, evts in channel_events.items():
        # Check each window, find the most severe threshold breach
        worst_type = None
        worst_backoff = 0
        worst_count = 0

        for window_name in ["hourly", "daily", "weekly"]:
            cfg = thresholds.get(window_name)
            if not cfg:
                continue

            threshold_count = cfg.get("count", 0)
            backoff_hours = cfg.get("backoff_hours", 0)
            if threshold_count <= 0:
                continue

            window_start = now - WINDOWS[window_name]
            window_events = [
                e for e in evts if e.event_timestamp and e.event_timestamp >= window_start
            ]
            count = len(window_events)

            if count >= threshold_count and backoff_hours > worst_backoff:
                worst_type = window_name
                worst_backoff = backoff_hours
                worst_count = count

        if worst_type and (channel, None) not in active_channels:
            # Check if already active for this specific channel
            already_active = any(
                e.channel == channel for e in active_entries
            )
            if already_active:
                # Escalation: update existing entry if new backoff is longer
                existing = next(
                    (e for e in active_entries if e.channel == channel), None
                )
                if existing and existing.reentry_at < now + timedelta(hours=worst_backoff):
                    existing.threshold_type = worst_type
                    existing.event_count = worst_count
                    existing.reentry_at = now + timedelta(hours=worst_backoff)
                    db.commit()
                    logger.info(
                        "Config %d: escalated blacklist for channel %d to %s (%dh backoff)",
                        config.id, channel, worst_type, worst_backoff,
                    )
                continue

            # Create new blacklist entry
            entry = DfsBlacklistEntry(
                config_id=config.id,
                channel=channel,
                threshold_type=worst_type,
                event_count=worst_count,
                blacklisted_at=now,
                reentry_at=now + timedelta(hours=worst_backoff),
                status="active",
            )
            db.add(entry)

            audit = DfsAuditLog(
                config_id=config.id,
                action="channel_blacklisted",
                details={
                    "channel": channel,
                    "threshold_type": worst_type,
                    "event_count": worst_count,
                    "backoff_hours": worst_backoff,
                    "reentry_at": (now + timedelta(hours=worst_backoff)).isoformat(),
                },
            )
            db.add(audit)
            db.commit()

            newly_blacklisted.append(channel)
            logger.info(
                "Config %d: blacklisted channel %d (%s threshold, %d events, %dh backoff)",
                config.id, channel, worst_type, worst_count, worst_backoff,
            )

            # Send Slack notification
            if config.slack_webhook_url:
                try:
                    from utils.slack import send_slack_message, build_dfs_blacklist_blocks
                    zone_name = ", ".join(
                        z.get("name", "?") for z in (config.zones or [])
                    )
                    blocks = build_dfs_blacklist_blocks(
                        channel=channel,
                        zone_name=zone_name,
                        threshold_type=worst_type,
                        event_count=worst_count,
                        backoff_hours=worst_backoff,
                        controller_name=controller_name,
                    )
                    await send_slack_message(
                        config.slack_webhook_url,
                        text=f"DFS Blacklist: Channel {channel} blacklisted ({worst_type}: {worst_count} events)",
                        blocks=blocks,
                    )
                except Exception as e:
                    logger.error("Failed to send Slack notification: %s", e)

    return newly_blacklisted


async def _sweep_expired(
    db, config: DfsBlacklistConfig, now: datetime, controller_name: str
) -> int:
    """
    Check for blacklist entries past their reentry_at time and expire them.

    Returns count of expired entries.
    """
    expired_entries = (
        db.query(DfsBlacklistEntry)
        .filter(
            DfsBlacklistEntry.config_id == config.id,
            DfsBlacklistEntry.status == "active",
            DfsBlacklistEntry.reentry_at <= now,
        )
        .all()
    )

    for entry in expired_entries:
        entry.status = "expired"
        entry.reentry_completed_at = now

        audit = DfsAuditLog(
            config_id=config.id,
            action="channel_expired",
            details={
                "channel": entry.channel,
                "threshold_type": entry.threshold_type,
                "blacklisted_at": entry.blacklisted_at.isoformat(),
                "reentry_at": entry.reentry_at.isoformat(),
            },
        )
        db.add(audit)

        logger.info(
            "Config %d: channel %d backoff expired, re-enabling",
            config.id, entry.channel,
        )

        # Slack notification for re-entry
        if config.slack_webhook_url:
            try:
                from utils.slack import send_slack_message, build_dfs_reentry_blocks
                zone_name = ", ".join(
                    z.get("name", "?") for z in (config.zones or [])
                )
                blocks = build_dfs_reentry_blocks(
                    channel=entry.channel,
                    zone_name=zone_name,
                    controller_name=controller_name,
                )
                await send_slack_message(
                    config.slack_webhook_url,
                    text=f"DFS Channel Re-enabled: Channel {entry.channel} backoff expired",
                    blocks=blocks,
                )
            except Exception as e:
                logger.error("Failed to send Slack reentry notification: %s", e)

    if expired_entries:
        db.commit()

    return len(expired_entries)


async def ensure_registered(scheduler) -> None:
    """Register the DFS blacklist job with the scheduler."""
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        if existing.trigger_config != TRIGGER_CONFIG:
            await scheduler.update_job(JOB_ID, trigger_config=TRIGGER_CONFIG)
            logger.info(f"Updated DFS blacklist trigger to {TRIGGER_CONFIG}")
        else:
            logger.info(f"DFS blacklist job '{JOB_ID}' already registered")
        return

    await scheduler.register_job(
        job_id=JOB_ID,
        name="DFS Blacklist Monitor",
        callable_path="jobs.dfs_blacklist_job:run_dfs_blacklist",
        trigger_type="interval",
        trigger_config=TRIGGER_CONFIG,
        owner_type="dfs_blacklist",
        description="Hourly: monitors DFS radar events and manages channel blacklisting",
    )
    logger.info(f"Registered DFS blacklist job '{JOB_ID}' (every 60 minutes)")
