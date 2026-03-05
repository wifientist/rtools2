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
    ignored_tenant_ids: List[str]

    class Config:
        from_attributes = True


class SettingsUpdate(BaseModel):
    target_aps: Optional[int] = None
    ignored_tenant_ids: Optional[List[str]] = None


class SnapshotBackfillEntry(BaseModel):
    date: str  # ISO date string, e.g. "2026-01-15"
    total_aps: int
    operational_aps: int = 0
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
        new_venues = progress_data["total_venues"]
        new_ecs = progress_data["total_ecs"]

        if last and (
            last.total_aps == new_aps
            and last.operational_aps == new_op
            and last.total_venues == new_venues
            and last.total_ecs == new_ecs
        ):
            return

        snapshot = MigrationDashboardSnapshot(
            controller_id=controller_id,
            total_aps=progress_data["total_aps"],
            operational_aps=progress_data.get("status_summary", {}).get("operational", 0),
            total_venues=progress_data["total_venues"],
            total_clients=progress_data.get("total_clients", 0),
            total_ecs=progress_data["total_ecs"],
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
            ignored_tenant_ids=settings.ignored_tenant_ids,
        )
    return SettingsResponse(target_aps=180000, ignored_tenant_ids=[])


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
    if body.ignored_tenant_ids is not None:
        settings.ignored_tenant_ids = body.ignored_tenant_ids

    db.commit()
    db.refresh(settings)

    return SettingsResponse(
        target_aps=settings.target_aps,
        ignored_tenant_ids=settings.ignored_tenant_ids,
    )


# ---------- Report schedule endpoints ----------


class ReportScheduleResponse(BaseModel):
    enabled: bool = False
    frequency: str = "weekly"
    day_of_week: int = 0
    recipients: list[str] = []


class ReportScheduleUpdate(BaseModel):
    enabled: Optional[bool] = None
    frequency: Optional[str] = None
    day_of_week: Optional[int] = None
    recipients: Optional[list[str]] = None


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
        return ReportScheduleResponse(
            enabled=report.enabled,
            frequency=report.frequency,
            day_of_week=report.day_of_week,
            recipients=report.recipients,
        )
    return ReportScheduleResponse()


@router.put("/report-schedule/{controller_id}", response_model=ReportScheduleResponse)
def update_report_schedule(
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

    db.commit()
    db.refresh(report)

    return ReportScheduleResponse(
        enabled=report.enabled,
        frequency=report.frequency,
        day_of_week=report.day_of_week,
        recipients=report.recipients,
    )


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
                "total_venues": s.total_venues,
                "total_clients": s.total_clients,
                "total_ecs": s.total_ecs,
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
        "ignored_tenant_ids": list(ignored_ids),
    }

    if not ec_list:
        empty_data = {
            "total_aps": 0, "total_venues": 0, "total_clients": 0,
            "total_switches": 0, "total_ecs": 0, "errors": 0,
            "status_summary": {"operational": 0, "offline": 0},
            "status_counts": {}, "tenants": [],
        }
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
        error = None

        async with semaphore:
            try:
                ap_result, venue_result, client_result, switch_result = await asyncio.gather(
                    asyncio.to_thread(
                        r1_client.post,
                        "/venues/aps/query",
                        payload={
                            "fields": ["status", "venueName", "venueId"],
                            "page": 0,
                            "pageSize": 5000,
                        },
                        override_tenant_id=tenant_id,
                    ),
                    asyncio.to_thread(
                        r1_client.get,
                        "/venues",
                        override_tenant_id=tenant_id,
                    ),
                    asyncio.to_thread(
                        r1_client.post,
                        "/venues/aps/clients/query",
                        payload={
                            "fields": ["macAddress"],
                            "page": 0,
                            "pageSize": 1,
                        },
                        override_tenant_id=tenant_id,
                    ),
                    asyncio.to_thread(
                        r1_client.post,
                        "/venues/switches/query",
                        payload={
                            "fields": ["serialNumber"],
                            "page": 0,
                            "pageSize": 1,
                        },
                        override_tenant_id=tenant_id,
                    ),
                    return_exceptions=True,
                )

                venue_map: dict[str, dict] = {}

                if not isinstance(ap_result, Exception) and ap_result.ok:
                    ap_data = ap_result.json()
                    ap_count = ap_data.get("totalCount", len(ap_data.get("data", [])))
                    for ap in ap_data.get("data", []):
                        s = ap.get("status", "Unknown")
                        status_counts[s] = status_counts.get(s, 0) + 1

                        # Group by venue
                        vid = ap.get("venueId", "unknown")
                        vname = ap.get("venueName", "Unknown Venue")
                        if vid not in venue_map:
                            venue_map[vid] = {
                                "venue_id": vid,
                                "venue_name": vname,
                                "ap_count": 0,
                                "operational": 0,
                                "offline": 0,
                            }
                        venue_map[vid]["ap_count"] += 1
                        if s.startswith("2_"):
                            venue_map[vid]["operational"] += 1
                        else:
                            venue_map[vid]["offline"] += 1
                elif isinstance(ap_result, Exception):
                    error = str(ap_result)

                if not isinstance(venue_result, Exception) and venue_result.ok:
                    venue_data = venue_result.json()
                    venue_list = []
                    if isinstance(venue_data, list):
                        venue_list = venue_data
                        venue_count = len(venue_data)
                    elif isinstance(venue_data, dict):
                        venue_list = venue_data.get("data", [])
                        venue_count = venue_data.get("totalCount", len(venue_list))

                    # Build name lookup and enrich venue_map / add 0-AP venues
                    for v in venue_list:
                        vid = v.get("id", "")
                        vname = v.get("name", "Unknown Venue")
                        if vid in venue_map:
                            venue_map[vid]["venue_name"] = vname
                        else:
                            venue_map[vid] = {
                                "venue_id": vid,
                                "venue_name": vname,
                                "ap_count": 0,
                                "operational": 0,
                                "offline": 0,
                            }
                elif isinstance(venue_result, Exception) and not error:
                    error = str(venue_result)

                if not isinstance(client_result, Exception) and client_result.ok:
                    client_data = client_result.json()
                    client_count = client_data.get("totalCount", 0)
                elif isinstance(client_result, Exception) and not error:
                    error = str(client_result)

                if not isinstance(switch_result, Exception) and switch_result.ok:
                    switch_data = switch_result.json()
                    switch_count = switch_data.get("totalCount", 0)
                elif isinstance(switch_result, Exception) and not error:
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
                "status_counts": {}, "venue_stats": [], "error": str(result),
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
    for t in active:
        for key in total_summary:
            total_summary[key] += t.get("status_summary", {}).get(key, 0)
        for code, count in t.get("status_counts", {}).items():
            total_status_counts[code] = total_status_counts.get(code, 0) + count

    progress_data = {
        "total_aps": total_aps,
        "total_venues": total_venues,
        "total_clients": total_clients,
        "total_switches": total_switches,
        "total_ecs": len(tenants),
        "errors": errors,
        "status_summary": total_summary,
        "status_counts": total_status_counts,
        "tenants": sorted(tenants, key=lambda t: t["ap_count"], reverse=True),
    }

    logger.info(
        f"[dashboard] Progress for controller={controller_id}: "
        f"{total_aps} APs, {total_switches} SWs, {total_summary['operational']} online, "
        f"{total_venues} venues, {total_clients} clients, {len(tenants)} ECs, {errors} errors"
    )

    # Auto-capture snapshot (throttled to once per 24h, skip if unchanged)
    _maybe_capture_snapshot(controller_id, progress_data, tenants, db)

    return progress_data, tenants, settings_data


# ---------- Progress endpoint ----------

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

@router.get("/movers/{controller_id}")
async def get_venue_movers(
    controller_id: int = Path(...),
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Compare venue statuses from N days ago to current and return transitions.

    Categories: new, pending_to_in_progress, pending_to_migrated, in_progress_to_migrated.
    """
    logger.info(f"[dashboard] GET movers controller={controller_id} days={days} user={current_user.email}")

    controller = validate_controller_access(controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Requires a RuckusONE controller")
    if controller.controller_subtype != "MSP":
        raise HTTPException(status_code=400, detail="Requires an MSP controller")

    cutoff = datetime.utcnow() - timedelta(days=days)
    VMH = VenueMigrationHistory

    # Query venues by transition type within the time window
    base = db.query(VMH).filter(VMH.controller_id == controller_id)

    def _to_venue(row: VenueMigrationHistory) -> dict:
        return {
            "venue_id": row.venue_id,
            "venue_name": row.venue_name,
            "tenant_name": row.tenant_name,
            "ap_count": row.ap_count,
            "operational": row.operational,
            "current_status": row.status,
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
