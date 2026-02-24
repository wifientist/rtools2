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
from clients.r1_client import create_r1_client_from_controller, validate_controller_access
from constants.access import MIGRATION_DASHBOARD_DOMAINS
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

def _check_dashboard_access(current_user: User):
    """Raise 403 if user is not a super and their domain isn't allowed."""
    user_domain = current_user.company.domain if current_user.company else None
    if current_user.role != RoleEnum.super and user_domain not in MIGRATION_DASHBOARD_DOMAINS:
        raise HTTPException(status_code=403, detail="Access restricted")


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
    _check_dashboard_access(current_user)
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
    _check_dashboard_access(current_user)
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
    _check_dashboard_access(current_user)
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
            "total_ecs": 0, "errors": 0,
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
        status_counts: dict[str, int] = {}
        error = None

        async with semaphore:
            try:
                ap_result, venue_result, client_result = await asyncio.gather(
                    asyncio.to_thread(
                        r1_client.post,
                        "/venues/aps/query",
                        payload={
                            "fields": ["status"],
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
                    return_exceptions=True,
                )

                if not isinstance(ap_result, Exception) and ap_result.ok:
                    ap_data = ap_result.json()
                    ap_count = ap_data.get("totalCount", len(ap_data.get("data", [])))
                    for ap in ap_data.get("data", []):
                        s = ap.get("status", "Unknown")
                        status_counts[s] = status_counts.get(s, 0) + 1
                elif isinstance(ap_result, Exception):
                    error = str(ap_result)

                if not isinstance(venue_result, Exception) and venue_result.ok:
                    venue_data = venue_result.json()
                    if isinstance(venue_data, list):
                        venue_count = len(venue_data)
                    elif isinstance(venue_data, dict):
                        venue_count = venue_data.get(
                            "totalCount", len(venue_data.get("data", []))
                        )
                elif isinstance(venue_result, Exception) and not error:
                    error = str(venue_result)

                if not isinstance(client_result, Exception) and client_result.ok:
                    client_data = client_result.json()
                    client_count = client_data.get("totalCount", 0)
                elif isinstance(client_result, Exception) and not error:
                    error = str(client_result)

            except Exception as e:
                logger.warning(f"Error fetching stats for EC '{tenant_name}': {e}")
                error = str(e)

        return {
            "id": tenant_id,
            "name": tenant_name,
            "ap_count": ap_count,
            "venue_count": venue_count,
            "client_count": client_count,
            "status_summary": _summarize_statuses(status_counts),
            "status_counts": status_counts,
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
                "ap_count": 0, "venue_count": 0, "client_count": 0,
                "status_summary": {"operational": 0, "offline": 0},
                "status_counts": {}, "error": str(result),
            }
        else:
            tenant = result
        tenant["ignored"] = tenant["id"] in ignored_ids
        tenants.append(tenant)

    active = [t for t in tenants if not t["ignored"]]
    total_aps = sum(t["ap_count"] for t in active)
    total_venues = sum(t["venue_count"] for t in active)
    total_clients = sum(t["client_count"] for t in active)
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
        "total_ecs": len(tenants),
        "errors": errors,
        "status_summary": total_summary,
        "status_counts": total_status_counts,
        "tenants": sorted(tenants, key=lambda t: t["ap_count"], reverse=True),
    }

    logger.info(
        f"[dashboard] Progress for controller={controller_id}: "
        f"{total_aps} APs, {total_summary['operational']} online, "
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
    Access restricted to specific companies and super users.

    Ignored tenants (from settings) are still returned but excluded from totals.
    """
    logger.info(f"[dashboard] GET progress controller={controller_id} user={current_user.email}")
    _check_dashboard_access(current_user)

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
