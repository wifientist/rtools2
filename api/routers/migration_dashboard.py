"""
Migration Dashboard — SZ to R1 Progress Tracking

Endpoints for migration progress across MSP EC tenants,
plus per-controller settings (target APs, ignored tenants).
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.user import User, RoleEnum
from models.controller import Controller
from models.migration_dashboard_settings import MigrationDashboardSettings
from models.migration_dashboard_snapshot import MigrationDashboardSnapshot
from models.venue_migration_history import VenueMigrationHistory
from clients.r1_client import create_r1_client_from_controller, validate_controller_access
from dependencies import get_db, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/migration-dashboard",
    tags=["Migration Dashboard"],
)


# ---------- Pydantic schemas ----------

class SettingsResponse(BaseModel):
    target_aps: int
    target_switches: int
    ignored_tenant_ids: List[str]
    sz_controller_ids: List[int] = []
    sz_min_zone_aps: int = 5
    sz_min_group_switches: int = 1

    class Config:
        from_attributes = True


class SettingsUpdate(BaseModel):
    target_aps: Optional[int] = None
    target_switches: Optional[int] = None
    ignored_tenant_ids: Optional[List[str]] = None
    sz_controller_ids: Optional[List[int]] = None
    sz_min_zone_aps: Optional[int] = None
    sz_min_group_switches: Optional[int] = None


class SnapshotBackfillEntry(BaseModel):
    date: str  # ISO date string, e.g. "2026-01-15"
    total_aps: int
    operational_aps: int = 0
    total_switches: int = 0
    operational_switches: int = 0
    total_venues: int = 0
    total_clients: int = 0
    total_ecs: int = 0


class SnapshotBackfillRequest(BaseModel):
    entries: List[SnapshotBackfillEntry]


# ---------- Helpers ----------

def _summarize_statuses(status_counts: dict[str, int]) -> dict[str, int]:
    """
    Group raw R1 AP status codes into severity buckets.

    Raw codes use prefix-based severity:
      1_xx = InSetupPhase (provisioned, initializing, offline)
      2_xx = Operational  (online, applying firmware/config)
      3_xx = RequiresAttention (update failed, disconnected)
      4_xx = TransientIssue (rebooting, heartbeat lost)
    """
    summary: dict[str, int] = {
        "operational": 0,
        "offline": 0,
    }
    for code, count in status_counts.items():
        if code.startswith("2_"):
            summary["operational"] += count
        else:
            summary["offline"] += count
    return summary


def _summarize_switch_statuses(status_counts: dict[str, int]) -> dict[str, int]:
    """
    Group raw R1 switch deviceStatus values into operational/offline buckets.

    Observed values: ONLINE (operational), OFFLINE, PREPROVISIONED, INITIALIZING.
    Only ONLINE counts as operational; everything else is not-yet-up / down.
    """
    summary: dict[str, int] = {
        "operational": 0,
        "offline": 0,
    }
    for code, count in status_counts.items():
        if code == "ONLINE":
            summary["operational"] += count
        else:
            summary["offline"] += count
    return summary


def _fetch_all_switches(r1_client, tenant_id: str) -> list[dict]:
    """
    Fetch all switches for a tenant with their deviceStatus, paginating past the
    1000-row page cap. Unlike the tenant-wide AP query, /venues/switches/query
    honors the `page` parameter, so simple pagination works (verified: pages are
    disjoint). Raises if the first page fails so the caller can record an error.
    """
    switches: list[dict] = []
    page = 0
    page_size = 1000
    while True:
        resp = r1_client.post(
            "/venues/switches/query",
            payload={
                "fields": ["serialNumber", "deviceStatus", "venueId", "clientCount"],
                "page": page,
                "pageSize": page_size,
            },
            override_tenant_id=tenant_id,
        )
        if not resp.ok:
            if page == 0:
                raise RuntimeError(f"switches query HTTP {resp.status_code}")
            break
        body = resp.json()
        rows = body.get("data", []) or []
        switches.extend(rows)
        total = body.get("totalCount", len(switches))
        if len(rows) < page_size or len(switches) >= total:
            break
        page += 1
        if page > 50:  # safety stop: 50k switches
            break
    return switches


def _compute_venue_status(ap_count: int, operational: int) -> str:
    """Compute venue migration status tier (mirrors frontend VenueRow logic)."""
    if ap_count <= 0 or operational <= 0:
        return "Pending"
    pct = (operational / ap_count) * 100
    if pct >= 95:
        return "Migrated"
    return "In Progress"


def _upsert_venue_history(
    controller_id: int, tenants: list[dict], db: Session
) -> None:
    """Update venue_migration_history rows from live tenant/venue data."""
    now = datetime.utcnow()

    # Load existing rows keyed by venue_id
    existing = {
        row.venue_id: row
        for row in db.query(VenueMigrationHistory).filter(
            VenueMigrationHistory.controller_id == controller_id
        ).all()
    }

    # Build current venue set from live data
    seen_venue_ids: set[str] = set()
    for t in tenants:
        if t.get("ignored"):
            continue
        for v in t.get("venue_stats", []):
            vid = v["venue_id"]
            seen_venue_ids.add(vid)
            ap_ct = v.get("ap_count", 0)
            op_ct = v.get("operational", 0)
            new_status = _compute_venue_status(ap_ct, op_ct)

            row = existing.get(vid)
            if row is None:
                # New venue — insert
                row = VenueMigrationHistory(
                    controller_id=controller_id,
                    venue_id=vid,
                    venue_name=v["venue_name"],
                    tenant_id=t["id"],
                    tenant_name=t["name"],
                    ap_count=ap_ct,
                    operational=op_ct,
                    status=new_status,
                    pending_at=now,
                )
                # Stamp transition dates if already past Pending
                if new_status in ("In Progress", "Migrated"):
                    row.in_progress_at = now
                if new_status == "Migrated":
                    row.migrated_at = now
                db.add(row)
            else:
                # Existing venue — update counts and check for status transition
                row.ap_count = ap_ct
                row.operational = op_ct
                row.venue_name = v["venue_name"]
                row.tenant_id = t["id"]
                row.tenant_name = t["name"]

                if row.status != new_status:
                    row.status = new_status
                    if new_status == "In Progress" and not row.in_progress_at:
                        row.in_progress_at = now
                    elif new_status == "Migrated" and not row.migrated_at:
                        row.migrated_at = now
                    # Clear removed_at if venue reappears
                    if row.removed_at and new_status != "Removed":
                        row.removed_at = None

    # Mark venues no longer in live data as Removed
    for vid, row in existing.items():
        if vid not in seen_venue_ids and row.status != "Removed":
            row.status = "Removed"
            if not row.removed_at:
                row.removed_at = now

    try:
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to upsert venue history for controller {controller_id}: {e}")
        db.rollback()


# One SZ controller's inventory walk; a hung controller must not hold up the dashboard.
SZ_COUNT_TIMEOUT_S = 120


def _start_sz_connected_count(
    sz_controller_ids: list[int],
    db: Session,
    min_zone_aps: int = 1,
    min_group_switches: int = 1,
):
    """
    Start counting connected APs (Online + Flagged) and online switches on the
    selected SZ controllers.

    These are the devices still left to migrate: new installs go straight to R1,
    so they never show up here. APs and switches carry separate errors so a
    switch-module failure doesn't throw away a good AP count.

    A zone counts toward "Zones" only with at least min_zone_aps connected APs,
    and a switch group toward "SG" only with at least min_group_switches online
    switches; smaller ones are stragglers and not counted. Device totals are
    never thresholded. Returns a task to await, or None when no SZ
    controllers are selected. All DB reads happen here, before the task starts,
    so the task only does network I/O while the R1 fan-out shares the session.
    """
    if not sz_controller_ids:
        return None
    from clients.sz_client_deps import create_sz_client_from_controller

    controllers = {
        c.id: c
        for c in db.query(Controller).filter(Controller.id.in_(sz_controller_ids)).all()
    }
    prepared = []  # (row, client or None)
    for cid in sz_controller_ids:
        ctrl = controllers.get(cid)
        row = {
            "id": cid,
            "name": ctrl.name if ctrl else f"Controller {cid}",
            "connected": None,
            "zones_read": 0,
            "zones_total": 0,
            "error": None,  # AP count (or login) failure
            "switches_online": None,
            "switches_total": 0,
            "switch_error": None,
            # Sites still holding an online device. Zones and switch groups
            # have unrelated IDs, so they are matched by name (case and
            # spacing ignored); a switch-only group counts as its own site.
            "zones_with_aps": None,       # zones at/above min_zone_aps
            "switch_groups_online": None, # groups at/above min_group_switches
            "sites": None,
        }
        client = None
        if not ctrl or ctrl.controller_type != "SmartZone":
            row["error"] = row["switch_error"] = "Not found, or not a SmartZone controller"
        else:
            try:
                client = create_sz_client_from_controller(cid, db)
            except Exception as e:
                row["error"] = row["switch_error"] = str(getattr(e, "detail", e)) or type(e).__name__
        prepared.append((row, client))

    async def count_one(row: dict, client) -> dict:
        if client is None:
            return row

        def describe(e: Exception) -> str:
            return str(getattr(e, "detail", e)) or type(e).__name__

        zone_counts: dict[str, int] = {}
        group_counts: dict[str, int] = {}

        def norm(name: str) -> str:
            return " ".join(name.split()).lower()

        def strip_sg(name: str) -> str:
            # Switch groups are often the zone name + " SG" ("1740 Division SG").
            return name[:-3] if name.endswith(" sg") else name

        async def run() -> None:
            async with client:
                try:
                    counts = await client.system.get_connected_ap_count()
                    row["connected"] = counts["connected"]
                    row["zones_read"] = counts["zones_read"]
                    row["zones_total"] = counts["zones_total"]
                    for n, k in counts.get("connected_by_zone_name", {}).items():
                        zone_counts[norm(n)] = zone_counts.get(norm(n), 0) + k
                    row["zones_with_aps"] = sum(1 for k in zone_counts.values() if k >= min_zone_aps)
                except Exception as e:
                    row["error"] = describe(e)
                try:
                    sw = await client.switches.get_switch_status_counts()
                    row["switches_online"] = sw["online"]
                    row["switches_total"] = sw["total"]
                    for n, k in sw.get("online_by_site_name", {}).items():
                        group_counts[norm(n)] = group_counts.get(norm(n), 0) + k
                    row["switch_groups_online"] = sum(
                        1 for k in group_counts.values() if k >= min_group_switches
                    )
                except Exception as e:
                    row["switch_error"] = describe(e)

        try:
            await asyncio.wait_for(run(), timeout=SZ_COUNT_TIMEOUT_S)
        except asyncio.TimeoutError:
            msg = f"Timed out after {SZ_COUNT_TIMEOUT_S}s"
            if row["connected"] is None and not row["error"]:
                row["error"] = msg
            if row["switches_online"] is None and not row["switch_error"]:
                row["switch_error"] = msg
        except Exception as e:  # login failure: neither count happened
            row["error"] = row["error"] or describe(e)
            row["switch_error"] = row["switch_error"] or describe(e)
        if not row["error"] and not row["switch_error"]:
            # Every zone over the minimum is a site; a switch group over its
            # minimum adds one only if it doesn't match one of those zones. The
            # " SG" suffix is ignored for that match alone, so two real zones
            # are never merged.
            zone_names = {z for z, k in zone_counts.items() if k >= min_zone_aps}
            group_names = {g for g, k in group_counts.items() if k >= min_group_switches}
            zone_keys = zone_names | {strip_sg(z) for z in zone_names}
            switch_only = {
                g for g in group_names
                if g not in zone_keys and strip_sg(g) not in zone_keys
            }
            row["sites"] = len(zone_names) + len(switch_only)
        if row["error"] or row["switch_error"]:
            logger.warning(
                f"[dashboard] SZ count failed for {row['name']} ({row['id']}): "
                f"aps={row['error']} switches={row['switch_error']}"
            )
        return row

    async def count_all() -> dict:
        rows = await asyncio.gather(*(count_one(r, c) for r, c in prepared))
        return {
            "connected_aps": sum(r["connected"] or 0 for r in rows),
            # Every controller answered; otherwise "remaining" is an undercount.
            "complete": all(r["error"] is None for r in rows),
            "online_switches": sum(r["switches_online"] or 0 for r in rows),
            "switches_complete": all(r["switch_error"] is None for r in rows),
            "controllers": sorted(rows, key=lambda r: r["name"].lower()),
            "min_zone_aps": min_zone_aps,
            "min_group_switches": min_group_switches,
        }

    return asyncio.ensure_future(count_all())


def _compute_ap_progress(total_aps: int, settings_data: dict, sz: dict | None) -> dict:
    """
    AP migration percentage.

    With SZ controllers selected: R1 / (R1 + still connected on SZ). The total
    moves with the fleet, so new R1 installs can't push it past 100%; it only
    reaches 100% when SZ has nothing connected left.
    Without them: the legacy R1 / fixed target.
    """
    if sz is None:
        target = settings_data.get("target_aps") or 0
        return {
            "mode": "target",
            "r1_aps": total_aps,
            "target_aps": target,
            "percentage": (total_aps / target * 100) if target > 0 else 0,
        }
    remaining = sz["connected_aps"]
    fleet_total = total_aps + remaining
    return {
        "mode": "sz",
        "r1_aps": total_aps,
        "sz_connected_aps": remaining,
        "fleet_total": fleet_total,
        "percentage": (total_aps / fleet_total * 100) if fleet_total > 0 else 0,
        "sz_complete": sz["complete"],
        "sz_controllers": sz["controllers"],
        "min_zone_aps": sz["min_zone_aps"],
        "min_group_switches": sz["min_group_switches"],
    }


def _compute_switch_progress(total_switches: int, settings_data: dict, sz: dict | None) -> dict:
    """Switch percentage, same rule as APs: R1 / (R1 + still online on SZ)."""
    if sz is None:
        target = settings_data.get("target_switches") or 0
        return {
            "mode": "target",
            "r1_switches": total_switches,
            "target_switches": target,
            "percentage": (total_switches / target * 100) if target > 0 else 0,
        }
    remaining = sz["online_switches"]
    fleet_total = total_switches + remaining
    return {
        "mode": "sz",
        "r1_switches": total_switches,
        "sz_online_switches": remaining,
        "fleet_total": fleet_total,
        "percentage": (total_switches / fleet_total * 100) if fleet_total > 0 else 0,
        "sz_complete": sz["switches_complete"],
    }


def _get_settings(controller_id: int, db: Session) -> MigrationDashboardSettings | None:
    return (
        db.query(MigrationDashboardSettings)
        .filter(MigrationDashboardSettings.controller_id == controller_id)
        .first()
    )


def _maybe_capture_snapshot(
    controller_id: int,
    progress_data: dict,
    tenants: list[dict],
    db: Session,
) -> None:
    """Save a daily snapshot if data has changed since the last one."""
    try:
        # A tenant that failed to read counts as 0 APs; saving that would
        # record a fake drop (2026-11-11: R1 retired GET /venues, every EC
        # errored, and the day's snapshot said 0 APs).
        if progress_data.get("errors"):
            logger.warning(
                f"Skipping snapshot for controller {controller_id}: "
                f"{progress_data['errors']} tenant(s) errored"
            )
            return
        cutoff = datetime.utcnow() - timedelta(hours=24)
        recent = (
            db.query(MigrationDashboardSnapshot)
            .filter(
                MigrationDashboardSnapshot.controller_id == controller_id,
                MigrationDashboardSnapshot.captured_at >= cutoff,
            )
            .first()
        )
        if recent:
            return

        # Check if anything changed vs the most recent snapshot
        last = (
            db.query(MigrationDashboardSnapshot)
            .filter(MigrationDashboardSnapshot.controller_id == controller_id)
            .order_by(MigrationDashboardSnapshot.captured_at.desc())
            .first()
        )
        new_aps = progress_data["total_aps"]
        new_op = progress_data.get("status_summary", {}).get("operational", 0)
        new_switches = progress_data.get("total_switches", 0)
        new_sw_op = progress_data.get("switch_status_summary", {}).get("operational", 0)
        new_venues = progress_data["total_venues"]
        new_ecs = progress_data["total_ecs"]
        ap_progress = progress_data.get("ap_progress") or {}
        # Only a full SZ answer is worth keeping; a partial sum would read as progress.
        new_sz = (
            ap_progress.get("sz_connected_aps")
            if ap_progress.get("mode") == "sz" and ap_progress.get("sz_complete")
            else None
        )
        switch_progress = progress_data.get("switch_progress") or {}
        new_sz_sw = (
            switch_progress.get("sz_online_switches")
            if switch_progress.get("mode") == "sz" and switch_progress.get("sz_complete")
            else None
        )

        if last and (
            last.total_aps == new_aps
            and last.operational_aps == new_op
            and last.total_switches == new_switches
            and last.operational_switches == new_sw_op
            and last.total_venues == new_venues
            and last.total_ecs == new_ecs
            and last.sz_connected_aps == new_sz
            and last.sz_online_switches == new_sz_sw
        ):
            return

        snapshot = MigrationDashboardSnapshot(
            controller_id=controller_id,
            total_aps=progress_data["total_aps"],
            operational_aps=progress_data.get("status_summary", {}).get("operational", 0),
            total_switches=progress_data.get("total_switches", 0),
            operational_switches=progress_data.get("switch_status_summary", {}).get("operational", 0),
            total_venues=progress_data["total_venues"],
            total_clients=progress_data.get("total_clients", 0),
            total_ecs=progress_data["total_ecs"],
            sz_connected_aps=new_sz,
            sz_online_switches=new_sz_sw,
            tenant_data=[
                {
                    "id": t["id"],
                    "name": t["name"],
                    "ap_count": t["ap_count"],
                    "venue_count": t["venue_count"],
                    "client_count": t.get("client_count", 0),
                    "operational": t.get("status_summary", {}).get("operational", 0),
                }
                for t in tenants
                if not t.get("ignored")
            ],
        )
        db.add(snapshot)
        db.commit()
        logger.info(f"Captured snapshot for controller {controller_id}")

        # Upsert venue migration history rows
        _upsert_venue_history(controller_id, tenants, db)
    except Exception as e:
        logger.warning(f"Failed to capture snapshot for controller {controller_id}: {e}")
        db.rollback()


# ---------- Settings endpoints ----------

@router.get("/settings/{controller_id}", response_model=SettingsResponse)
def get_settings(
    controller_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get dashboard settings for a controller (returns defaults if none saved)."""
    logger.info(f"[dashboard] GET settings controller={controller_id} user={current_user.email}")
    validate_controller_access(controller_id, current_user, db)

    settings = _get_settings(controller_id, db)
    if settings:
        return SettingsResponse(
            target_aps=settings.target_aps,
            target_switches=settings.target_switches,
            ignored_tenant_ids=settings.ignored_tenant_ids,
            sz_controller_ids=settings.sz_controller_ids or [],
            sz_min_zone_aps=settings.sz_min_zone_aps,
            sz_min_group_switches=settings.sz_min_group_switches,
        )
    return SettingsResponse(target_aps=180000, target_switches=10000, ignored_tenant_ids=[], sz_controller_ids=[])


@router.put("/settings/{controller_id}", response_model=SettingsResponse)
def update_settings(
    body: SettingsUpdate,
    controller_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upsert dashboard settings for a controller."""
    logger.info(f"[dashboard] PUT settings controller={controller_id} user={current_user.email} body={body.dict()}")
    validate_controller_access(controller_id, current_user, db)

    settings = _get_settings(controller_id, db)
    if not settings:
        settings = MigrationDashboardSettings(controller_id=controller_id)
        db.add(settings)

    if body.target_aps is not None:
        settings.target_aps = body.target_aps
    if body.target_switches is not None:
        settings.target_switches = body.target_switches
    if body.ignored_tenant_ids is not None:
        settings.ignored_tenant_ids = body.ignored_tenant_ids
    if body.sz_controller_ids is not None:
        sz_ids = sorted(set(body.sz_controller_ids))
        for sz_id in sz_ids:
            sz_ctrl = validate_controller_access(sz_id, current_user, db)
            if sz_ctrl.controller_type != "SmartZone":
                raise HTTPException(
                    status_code=400,
                    detail=f"'{sz_ctrl.name}' is not a SmartZone controller",
                )
        settings.sz_controller_ids = sz_ids
    if body.sz_min_zone_aps is not None:
        settings.sz_min_zone_aps = max(1, body.sz_min_zone_aps)
    if body.sz_min_group_switches is not None:
        settings.sz_min_group_switches = max(1, body.sz_min_group_switches)

    db.commit()
    db.refresh(settings)

    return SettingsResponse(
        target_aps=settings.target_aps,
        target_switches=settings.target_switches,
        ignored_tenant_ids=settings.ignored_tenant_ids,
        sz_controller_ids=settings.sz_controller_ids or [],
        sz_min_zone_aps=settings.sz_min_zone_aps,
        sz_min_group_switches=settings.sz_min_group_switches,
    )


# ---------- Report schedule endpoints ----------


class ReportScheduleResponse(BaseModel):
    enabled: bool = False
    frequency: str = "weekly"
    day_of_week: int = 0
    recipients: list[str] = []
    send_time: str = "12:30"  # local "HH:MM"
    timezone: str = "UTC"     # IANA name, so DST shifts with the clock


class ReportScheduleUpdate(BaseModel):
    enabled: Optional[bool] = None
    frequency: Optional[str] = None
    day_of_week: Optional[int] = None
    recipients: Optional[list[str]] = None
    send_time: Optional[str] = None
    timezone: Optional[str] = None


def _schedule_response(report) -> ReportScheduleResponse:
    return ReportScheduleResponse(
        enabled=report.enabled,
        frequency=report.frequency,
        day_of_week=report.day_of_week,
        recipients=report.recipients,
        send_time=report.send_time,
        timezone=report.timezone,
    )


@router.get("/report-schedule/{controller_id}", response_model=ReportScheduleResponse)
def get_report_schedule(
    controller_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get report schedule for a controller (returns defaults if none configured)."""
    from models.scheduled_report import ScheduledReport

    logger.info(f"[dashboard] GET report-schedule controller={controller_id} user={current_user.email}")
    validate_controller_access(controller_id, current_user, db)

    report = (
        db.query(ScheduledReport)
        .filter(
            ScheduledReport.report_type == "migration",
            ScheduledReport.context_id == str(controller_id),
        )
        .first()
    )
    if report:
        return _schedule_response(report)
    return ReportScheduleResponse()


@router.put("/report-schedule/{controller_id}", response_model=ReportScheduleResponse)
async def update_report_schedule(
    body: ReportScheduleUpdate,
    controller_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upsert report schedule for a controller."""
    from models.scheduled_report import ScheduledReport

    logger.info(f"[dashboard] PUT report-schedule controller={controller_id} user={current_user.email} body={body.dict()}")
    validate_controller_access(controller_id, current_user, db)

    report = (
        db.query(ScheduledReport)
        .filter(
            ScheduledReport.report_type == "migration",
            ScheduledReport.context_id == str(controller_id),
        )
        .first()
    )
    if not report:
        report = ScheduledReport(
            report_type="migration",
            context_id=str(controller_id),
            owner_id=current_user.id,
        )
        db.add(report)

    if body.enabled is not None:
        report.enabled = body.enabled
    if body.frequency is not None:
        report.frequency = body.frequency
    if body.day_of_week is not None:
        report.day_of_week = body.day_of_week
    if body.recipients is not None:
        report.recipients = body.recipients
    if body.send_time is not None:
        import re
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", body.send_time.strip())
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise HTTPException(status_code=400, detail="Send time must be HH:MM (24-hour)")
        report.send_time = f"{int(m.group(1)):02d}:{m.group(2)}"
    if body.timezone is not None:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(status_code=400, detail=f"Unknown time zone '{body.timezone}'")
        report.timezone = body.timezone

    db.commit()
    db.refresh(report)

    # The schedule's own cron job fires only when the schedule says so.
    from jobs.report_dispatcher_job import sync_report_job
    from scheduler.service import get_scheduler
    try:
        await sync_report_job(get_scheduler(), report)
    except Exception as e:
        logger.error(f"[dashboard] report {report.id} saved but not scheduled: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Schedule saved, but it could not be scheduled: {e}")

    return _schedule_response(report)


@router.post("/report/{controller_id}/send")
async def send_report_now(
    controller_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger a migration report for testing."""
    from models.scheduled_report import ScheduledReport
    from services.report_engine import generate_and_send_report

    logger.info(f"[dashboard] Manual report trigger controller={controller_id} user={current_user.email}")
    validate_controller_access(controller_id, current_user, db)

    report = (
        db.query(ScheduledReport)
        .filter(
            ScheduledReport.report_type == "migration",
            ScheduledReport.context_id == str(controller_id),
        )
        .first()
    )
    if not report or not report.recipients:
        raise HTTPException(status_code=400, detail="No report recipients configured. Save a schedule with recipients first.")

    result = await generate_and_send_report(report, db)
    return result


# ---------- Snapshots endpoint ----------

@router.get("/snapshots/{controller_id}")
def get_snapshots(
    controller_id: int = Path(...),
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get historical snapshots for a controller (default 30 days, max 365)."""
    logger.info(f"[dashboard] GET snapshots controller={controller_id} days={days} user={current_user.email}")
    validate_controller_access(controller_id, current_user, db)

    cutoff = datetime.utcnow() - timedelta(days=days)
    snapshots = (
        db.query(MigrationDashboardSnapshot)
        .filter(
            MigrationDashboardSnapshot.controller_id == controller_id,
            MigrationDashboardSnapshot.captured_at >= cutoff,
        )
        .order_by(MigrationDashboardSnapshot.captured_at.asc())
        .all()
    )

    logger.info(f"[dashboard] Returning {len(snapshots)} snapshots for controller={controller_id}")

    return {
        "status": "success",
        "data": [
            {
                "id": s.id,
                "captured_at": s.captured_at.isoformat(),
                "total_aps": s.total_aps,
                "operational_aps": s.operational_aps,
                "total_switches": s.total_switches,
                "operational_switches": s.operational_switches,
                "total_venues": s.total_venues,
                "total_clients": s.total_clients,
                "total_ecs": s.total_ecs,
                "sz_connected_aps": s.sz_connected_aps,
                "sz_online_switches": s.sz_online_switches,
            }
            for s in snapshots
        ],
    }


@router.post("/snapshots/{controller_id}/backfill")
def backfill_snapshots(
    body: SnapshotBackfillRequest,
    controller_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Insert historical snapshot data points. Requires super role."""
    logger.info(f"[dashboard] POST backfill controller={controller_id} entries={len(body.entries)} user={current_user.email}")
    if current_user.role != RoleEnum.super:
        raise HTTPException(status_code=403, detail="Super role required for backfill")
    validate_controller_access(controller_id, current_user, db)

    inserted = 0
    for entry in body.entries:
        try:
            captured_at = datetime.fromisoformat(entry.date).replace(hour=12, minute=0, second=0)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date: {entry.date}")

        # Skip if a snapshot already exists for this date
        existing = (
            db.query(MigrationDashboardSnapshot)
            .filter(
                MigrationDashboardSnapshot.controller_id == controller_id,
                MigrationDashboardSnapshot.captured_at >= captured_at.replace(hour=0),
                MigrationDashboardSnapshot.captured_at < captured_at.replace(hour=23, minute=59, second=59),
            )
            .first()
        )
        if existing:
            continue

        snapshot = MigrationDashboardSnapshot(
            controller_id=controller_id,
            total_aps=entry.total_aps,
            operational_aps=entry.operational_aps,
            total_switches=entry.total_switches,
            operational_switches=entry.operational_switches,
            total_venues=entry.total_venues,
            total_clients=entry.total_clients,
            total_ecs=entry.total_ecs,
            tenant_data=[],
            captured_at=captured_at,
        )
        db.add(snapshot)
        inserted += 1

    db.commit()
    skipped = len(body.entries) - inserted
    logger.info(f"[dashboard] Backfill complete controller={controller_id}: inserted={inserted} skipped={skipped}")
    return {"status": "success", "inserted": inserted, "skipped": skipped}


@router.delete("/snapshots/{controller_id}/{snapshot_id}")
def delete_snapshot(
    controller_id: int = Path(...),
    snapshot_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a single snapshot. Requires super role."""
    logger.info(f"[dashboard] DELETE snapshot controller={controller_id} snapshot={snapshot_id} user={current_user.email}")
    if current_user.role != RoleEnum.super:
        raise HTTPException(status_code=403, detail="Super role required")
    validate_controller_access(controller_id, current_user, db)

    snapshot = (
        db.query(MigrationDashboardSnapshot)
        .filter(
            MigrationDashboardSnapshot.id == snapshot_id,
            MigrationDashboardSnapshot.controller_id == controller_id,
        )
        .first()
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    db.delete(snapshot)
    db.commit()
    return {"status": "success"}


# ---------- Core progress fetcher (used by endpoint + scheduled job) ----------

async def fetch_controller_progress(
    controller_id: int, db: Session
) -> tuple[dict, list[dict], dict]:
    """
    Fetch migration progress for an MSP controller.

    Returns (progress_data, tenants, settings_data).
    Captures a snapshot as a side effect (throttled to once per 24h).
    """
    logger.info(f"[dashboard] Fetching progress for controller={controller_id}")
    r1_client = create_r1_client_from_controller(controller_id, db)

    # 1. Get all EC tenants
    ecs_response = await r1_client.msp.get_msp_ecs()
    ec_list = ecs_response.get("data", [])
    logger.info(f"[dashboard] Found {len(ec_list)} EC tenants for controller={controller_id}")

    # Load settings (ignored tenants, target)
    settings = _get_settings(controller_id, db)
    ignored_ids = set(settings.ignored_tenant_ids) if settings else set()
    settings_data = {
        "target_aps": settings.target_aps if settings else 180000,
        "target_switches": settings.target_switches if settings else 10000,
        "ignored_tenant_ids": list(ignored_ids),
        "sz_controller_ids": list(settings.sz_controller_ids or []) if settings else [],
        "sz_min_zone_aps": settings.sz_min_zone_aps if settings else 5,
        "sz_min_group_switches": settings.sz_min_group_switches if settings else 1,
    }

    # SZ counts run alongside the R1 fan-out below.
    sz_task = _start_sz_connected_count(
        settings_data["sz_controller_ids"],
        db,
        min_zone_aps=settings_data["sz_min_zone_aps"],
        min_group_switches=settings_data["sz_min_group_switches"],
    )

    if not ec_list:
        empty_data = {
            "total_aps": 0, "total_venues": 0, "total_clients": 0,
            "total_switches": 0, "total_ecs": 0, "errors": 0,
            "status_summary": {"operational": 0, "offline": 0},
            "status_counts": {},
            "switch_status_summary": {"operational": 0, "offline": 0},
            "switch_status_counts": {}, "tenants": [],
        }
        sz = await sz_task if sz_task else None
        empty_data["ap_progress"] = _compute_ap_progress(0, settings_data, sz)
        empty_data["switch_progress"] = _compute_switch_progress(0, settings_data, sz)
        return empty_data, [], settings_data

    # 2. Query each EC for AP count and venue count in parallel
    semaphore = asyncio.Semaphore(10)

    async def fetch_ec_stats(ec: dict) -> dict:
        tenant_id = ec["id"]
        tenant_name = ec.get("name", "Unknown")
        ap_count = 0
        venue_count = 0
        client_count = 0
        switch_count = 0
        status_counts: dict[str, int] = {}
        switch_status_counts: dict[str, int] = {}
        error = None

        async with semaphore:
            try:
                # Fetch venue list first — its IDs drive the per-venue AP fanout.
                # (The tenant-wide /venues/aps/query call caps at ~1000 rows and
                # ignores the `page` parameter, so per-venue is the only way to
                # get complete results.)
                venue_list = []
                venue_map: dict[str, dict] = {}

                try:
                    venue_list = await asyncio.to_thread(
                        r1_client.venues.query_all_venues,
                        tenant_id,
                        ["id", "name"],
                    )
                except Exception as ve:
                    error = f"venues query failed: {ve}"

                if not error:
                    venue_count = len(venue_list)
                    # Pre-seed venue_map so 0-AP venues still appear in venue_stats.
                    for v in venue_list:
                        vid = v.get("id", "")
                        vname = v.get("name", "Unknown Venue")
                        venue_map[vid] = {
                            "venue_id": vid,
                            "venue_name": vname,
                            "ap_count": 0,
                            "operational": 0,
                            "offline": 0,
                            "client_count": 0,
                        }

                venue_ids_list = [v.get("id") for v in venue_list if v.get("id")]

                # Run APs (per-venue fanout) + switches in parallel.
                # /venues/aps/query caps tenant-wide responses at 1000 rows, so the
                # AP side has to fan out. Client counts are taken from each AP's
                # `clientCount` field in the same fetch — the dedicated clients
                # endpoint (/venues/aps/clients/query) caps `totalCount` at 10000
                # per venue AND per tenant, so per-AP aggregation is the only
                # cheap way to get a real number. clientCount is a live snapshot
                # of currently-connected clients per device, which matches what
                # device management displays.
                ap_result, switch_result = await asyncio.gather(
                    asyncio.to_thread(
                        r1_client.venues.query_all_aps_by_tenant,
                        tenant_id,
                        venue_ids_list,
                        ["status", "venueName", "venueId", "clientCount"],
                    ),
                    asyncio.to_thread(
                        _fetch_all_switches,
                        r1_client,
                        tenant_id,
                    ),
                    return_exceptions=True,
                )

                if not isinstance(ap_result, Exception):
                    ap_list = ap_result or []
                    ap_count = len(ap_list)
                    for ap in ap_list:
                        s = ap.get("status", "Unknown")
                        status_counts[s] = status_counts.get(s, 0) + 1

                        ap_clients = int(ap.get("clientCount") or 0)
                        client_count += ap_clients

                        vid = ap.get("venueId", "unknown")
                        vname = ap.get("venueName", "Unknown Venue")
                        if vid not in venue_map:
                            venue_map[vid] = {
                                "venue_id": vid,
                                "venue_name": vname,
                                "ap_count": 0,
                                "operational": 0,
                                "offline": 0,
                                "client_count": 0,
                            }
                        venue_map[vid]["ap_count"] += 1
                        venue_map[vid]["client_count"] = (
                            venue_map[vid].get("client_count", 0) + ap_clients
                        )
                        if s.startswith("2_"):
                            venue_map[vid]["operational"] += 1
                        else:
                            venue_map[vid]["offline"] += 1
                elif not error:
                    error = str(ap_result)

                if not isinstance(switch_result, Exception):
                    switch_list = switch_result or []
                    switch_count = len(switch_list)
                    for sw in switch_list:
                        ss = sw.get("deviceStatus", "Unknown")
                        switch_status_counts[ss] = switch_status_counts.get(ss, 0) + 1
                elif not error:
                    error = str(switch_result)

            except Exception as e:
                logger.warning(f"Error fetching stats for EC '{tenant_name}': {e}")
                error = str(e)

        return {
            "id": tenant_id,
            "name": tenant_name,
            "ap_count": ap_count,
            "venue_count": venue_count,
            "client_count": client_count,
            "switch_count": switch_count,
            "status_summary": _summarize_statuses(status_counts),
            "status_counts": status_counts,
            "switch_status_summary": _summarize_switch_statuses(switch_status_counts),
            "switch_status_counts": switch_status_counts,
            "venue_stats": sorted(venue_map.values(), key=lambda v: v["ap_count"], reverse=True),
            "error": error,
        }

    # 3. Execute all EC queries in parallel
    ec_stats = await asyncio.gather(
        *[fetch_ec_stats(ec) for ec in ec_list],
        return_exceptions=True,
    )

    # 4. Process results — mark ignored tenants, exclude from totals
    tenants = []
    for i, result in enumerate(ec_stats):
        if isinstance(result, Exception):
            tenant = {
                "id": ec_list[i]["id"],
                "name": ec_list[i].get("name", "Unknown"),
                "ap_count": 0, "venue_count": 0, "client_count": 0, "switch_count": 0,
                "status_summary": {"operational": 0, "offline": 0},
                "status_counts": {},
                "switch_status_summary": {"operational": 0, "offline": 0},
                "switch_status_counts": {},
                "venue_stats": [], "error": str(result),
            }
        else:
            tenant = result
        tenant["ignored"] = tenant["id"] in ignored_ids
        tenants.append(tenant)

    active = [t for t in tenants if not t["ignored"]]
    total_aps = sum(t["ap_count"] for t in active)
    total_venues = sum(t["venue_count"] for t in active)
    total_clients = sum(t["client_count"] for t in active)
    total_switches = sum(t.get("switch_count", 0) for t in active)
    errors = sum(1 for t in tenants if t.get("error"))

    total_summary = {"operational": 0, "offline": 0}
    total_status_counts: dict[str, int] = {}
    total_switch_summary = {"operational": 0, "offline": 0}
    total_switch_status_counts: dict[str, int] = {}
    for t in active:
        for key in total_summary:
            total_summary[key] += t.get("status_summary", {}).get(key, 0)
        for code, count in t.get("status_counts", {}).items():
            total_status_counts[code] = total_status_counts.get(code, 0) + count
        for key in total_switch_summary:
            total_switch_summary[key] += t.get("switch_status_summary", {}).get(key, 0)
        for code, count in t.get("switch_status_counts", {}).items():
            total_switch_status_counts[code] = total_switch_status_counts.get(code, 0) + count

    progress_data = {
        "total_aps": total_aps,
        "total_venues": total_venues,
        "total_clients": total_clients,
        "total_switches": total_switches,
        "total_ecs": len(tenants),
        "errors": errors,
        "status_summary": total_summary,
        "status_counts": total_status_counts,
        "switch_status_summary": total_switch_summary,
        "switch_status_counts": total_switch_status_counts,
        "tenants": sorted(tenants, key=lambda t: t["ap_count"], reverse=True),
    }
    sz = await sz_task if sz_task else None
    progress_data["ap_progress"] = _compute_ap_progress(total_aps, settings_data, sz)
    progress_data["switch_progress"] = _compute_switch_progress(total_switches, settings_data, sz)

    logger.info(
        f"[dashboard] Progress for controller={controller_id}: "
        f"{total_aps} APs, {total_switches} SWs, {total_summary['operational']} online, "
        f"{total_venues} venues, {total_clients} clients, {len(tenants)} ECs, {errors} errors"
    )

    # Reconciliation (diagnostic only, never affects what the dashboard shows):
    # cross-check the per-venue AP total against the MSP-wide
    # /tenants/inventories/query. If the gap is non-trivial, the inventory
    # breakdown by connectionStatus usually explains it (e.g. devices in
    # "Never Contacted Cloud" which per-venue queries miss).
    logger.info(f"[dashboard] reconciliation start controller={controller_id}")
    try:
        inventory = await asyncio.to_thread(r1_client.msp.get_inventory_summary)
        inv_total = inventory.get("total_count", 0)
        delta = inv_total - total_aps
        logger.info(
            f"[dashboard] reconciliation controller={controller_id} "
            f"per-venue={total_aps} msp-inventory={inv_total} delta={delta:+d} "
            f"inv_error={inventory.get('error')} "
            f"by_connection_status={inventory.get('by_connection_status', {})} "
            f"by_device_type={inventory.get('by_device_type', {})}"
        )
    except Exception as rec_err:
        logger.warning(
            f"[dashboard] reconciliation failed controller={controller_id}: "
            f"{type(rec_err).__name__}: {rec_err}",
            exc_info=True,
        )

    # Auto-capture snapshot (throttled to once per 24h, skip if unchanged)
    _maybe_capture_snapshot(controller_id, progress_data, tenants, db)

    return progress_data, tenants, settings_data


# ---------- Progress endpoint ----------

@router.get("/licenses/{controller_id}")
async def get_migration_licenses(
    controller_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get MSP-wide license compliance summary for a controller.

    Returns total paid / used / available license counts, upcoming
    expirations, and a per-device-type breakdown (WIFI/SWITCH/etc).
    Intended for a dedicated Licenses tab on the Migration Dashboard.

    Refresh cadence is independent from /progress so the license tab
    doesn't force the expensive per-venue AP fanout on every load.
    """
    logger.info(f"[dashboard] GET licenses controller={controller_id} user={current_user.email}")

    controller = validate_controller_access(controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Requires a RuckusONE controller")
    if controller.controller_subtype != "MSP":
        raise HTTPException(
            status_code=400,
            detail=f"Requires an MSP controller. '{controller.name}' is type '{controller.controller_subtype}'.",
        )

    r1_client = create_r1_client_from_controller(controller_id, db)
    summary = await asyncio.to_thread(r1_client.entitlements.get_msp_compliance_summary)

    return {
        "status": "success",
        "data": summary,
    }


@router.get("/progress/{controller_id}")
async def get_migration_progress(
    controller_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get migration progress across all EC tenants for an MSP controller.

    Returns AP counts and venue counts per EC tenant, plus totals.
    Requires an MSP-type RuckusONE controller.
    Ignored tenants (from settings) are still returned but excluded from totals.
    """
    logger.info(f"[dashboard] GET progress controller={controller_id} user={current_user.email}")

    controller = validate_controller_access(controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Requires a RuckusONE controller")
    if controller.controller_subtype != "MSP":
        raise HTTPException(
            status_code=400,
            detail=f"Requires an MSP controller. '{controller.name}' is type '{controller.controller_subtype}'.",
        )

    progress_data, tenants, settings_data = await fetch_controller_progress(
        controller_id, db
    )

    return {
        "status": "success",
        "data": progress_data,
        "settings": settings_data,
    }


# ---------- Movers & Shakers endpoint ----------

def _compute_cutoff(days: Optional[int] = None, since: Optional[str] = None) -> datetime:
    """Compute the cutoff datetime from either days or a named anchor like 'sunday'."""
    if since == "sunday":
        now = datetime.utcnow()
        days_since_sunday = (now.weekday() + 1) % 7  # Monday=0 → 1, Sunday=6 → 0
        sunday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_sunday)
        return sunday
    return datetime.utcnow() - timedelta(days=days or 30)


@router.get("/movers/{controller_id}")
async def get_venue_movers(
    controller_id: int = Path(...),
    days: Optional[int] = Query(None, ge=1, le=365),
    since: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Compare venue statuses from N days ago (or since a named anchor) to current and return transitions.

    Categories: new, pending_to_in_progress, pending_to_migrated, in_progress_to_migrated.
    Use `since=sunday` for week-to-date, or `days=N` for a rolling window.
    """
    logger.info(f"[dashboard] GET movers controller={controller_id} days={days} since={since} user={current_user.email}")

    controller = validate_controller_access(controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Requires a RuckusONE controller")
    if controller.controller_subtype != "MSP":
        raise HTTPException(status_code=400, detail="Requires an MSP controller")

    cutoff = _compute_cutoff(days, since)
    VMH = VenueMigrationHistory

    # Exclude ignored tenants
    settings = db.query(MigrationDashboardSettings).filter_by(controller_id=controller_id).first()
    ignored_ids = set(settings.ignored_tenant_ids) if settings and settings.ignored_tenant_ids else set()

    # Query venues by transition type within the time window
    base = db.query(VMH).filter(VMH.controller_id == controller_id)
    if ignored_ids:
        base = base.filter(VMH.tenant_id.notin_(ignored_ids))

    def _to_venue(row: VenueMigrationHistory) -> dict:
        return {
            "venue_id": row.venue_id,
            "venue_name": row.venue_name,
            "tenant_name": row.tenant_name,
            "ap_count": row.ap_count,
            "operational": row.operational,
            "current_status": row.status,
            "pending_at": row.pending_at.isoformat() if row.pending_at else None,
            "in_progress_at": row.in_progress_at.isoformat() if row.in_progress_at else None,
            "migrated_at": row.migrated_at.isoformat() if row.migrated_at else None,
        }

    new_venues = base.filter(VMH.pending_at >= cutoff).all()
    p_to_ip = base.filter(
        VMH.in_progress_at >= cutoff,
        VMH.in_progress_at != VMH.pending_at,  # exclude venues that were born In Progress
    ).all()
    p_to_m = base.filter(
        VMH.migrated_at >= cutoff,
        VMH.in_progress_at.is_(None),
    ).all()
    ip_to_m = base.filter(
        VMH.migrated_at >= cutoff,
        VMH.in_progress_at.isnot(None),
    ).all()

    transitions = {
        "new": [_to_venue(r) for r in new_venues],
        "pending_to_in_progress": [_to_venue(r) for r in p_to_ip],
        "pending_to_migrated": [_to_venue(r) for r in p_to_m],
        "in_progress_to_migrated": [_to_venue(r) for r in ip_to_m],
    }
    summary = {k: len(v) for k, v in transitions.items()}

    return {
        "status": "success",
        "baseline_date": cutoff.isoformat(),
        "current_date": datetime.utcnow().isoformat(),
        "days": days,
        "transitions": transitions,
        "summary": summary,
    }
