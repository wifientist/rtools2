"""
Pop and Swap Router — AP replacement tool endpoints.

Stage 1 (immediate): Snapshot old AP config, provision new AP, store swap record.
Stage 2 (deferred): Background poller or manual sync applies config when new AP is online.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session

from r1api.client import R1Client
from clients.r1_client import get_dynamic_r1_client, create_r1_client_from_controller
from dependencies import get_current_user, get_db
from models.user import User, RoleEnum
from models.controller import Controller
from redis_client import get_redis_client

from .schemas import (
    PopSwapPreviewRequest,
    PopSwapApplyRequest,
    PopSwapPreviewResponse,
    SwapPairPreview,
    SwapRecordSummary,
    SwapRecordDetail,
    SyncNowResponse,
    ExtendResponse,
)
from .swap_store import SwapStore
from .config_snapshot import capture_ap_snapshot
from .config_sync import apply_config_to_ap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pop-swap", tags=["Pop and Swap"])


# ============================================================================
# Stage 1: Snapshot + Provision
# ============================================================================

@router.get("/{controller_id}/venue/{venue_id}/aps")
async def list_venue_aps(
    controller_id: int,
    venue_id: str,
    r1_client: R1Client = Depends(get_dynamic_r1_client),
    current_user: User = Depends(get_current_user),
):
    """List all APs in a venue for selection."""
    tenant_id = r1_client.tenant_id
    aps = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
    return aps


@router.post("/{controller_id}/venue/{venue_id}/preview", response_model=PopSwapPreviewResponse)
async def preview_swap(
    controller_id: int,
    venue_id: str,
    request: PopSwapPreviewRequest,
    r1_client: R1Client = Depends(get_dynamic_r1_client),
    current_user: User = Depends(get_current_user),
):
    """
    Preview a Pop and Swap operation.

    Validates all serial numbers, captures a snapshot preview from old APs,
    and reports any warnings or errors.
    """
    tenant_id = r1_client.tenant_id
    venues_service = r1_client.venues

    # Validate no duplicate serials
    old_serials = [m.old_serial for m in request.mappings]
    new_serials = [m.new_serial for m in request.mappings]
    all_serials = old_serials + new_serials

    warnings = []
    if len(set(old_serials)) != len(old_serials):
        raise HTTPException(status_code=400, detail="Duplicate old serial numbers in mappings")
    if len(set(new_serials)) != len(new_serials):
        raise HTTPException(status_code=400, detail="Duplicate new serial numbers in mappings")
    if set(old_serials) & set(new_serials):
        raise HTTPException(status_code=400, detail="A serial number cannot appear as both old and new")

    # Fetch all APs in venue for lookup
    all_aps = await venues_service.get_aps_by_tenant_venue(tenant_id, venue_id)
    ap_lookup = {}
    if isinstance(all_aps, dict) and "data" in all_aps:
        ap_list = all_aps["data"]
    elif isinstance(all_aps, list):
        ap_list = all_aps
    else:
        ap_list = []
    for ap in ap_list:
        serial = ap.get("serialNumber", "")
        if serial:
            ap_lookup[serial] = ap

    pairs = []
    for mapping in request.mappings:
        pair = SwapPairPreview(
            old_serial=mapping.old_serial,
            new_serial=mapping.new_serial,
        )

        # Validate old AP exists in venue
        old_ap = ap_lookup.get(mapping.old_serial)
        if not old_ap:
            pair.errors.append(f"Old AP {mapping.old_serial} not found in venue")
            pair.valid = False
        else:
            pair.old_ap_name = old_ap.get("name", "")
            pair.old_ap_group_id = old_ap.get("apGroupId", "")
            pair.old_ap_group_name = old_ap.get("apGroupName", "")
            pair.old_ap_model = old_ap.get("model", "")
            pair.old_ap_status = old_ap.get("status", "")

            if pair.old_ap_status and pair.old_ap_status.lower() != "online":
                pair.warnings.append(f"Old AP is {pair.old_ap_status} — snapshot may have stale data")

            # Estimate settings count (we'll capture the real count during apply)
            pair.settings_count = 16  # Approximate: 15 settings + LAN ports

        # Check if new AP is already in venue (warning, not error)
        new_ap = ap_lookup.get(mapping.new_serial)
        if new_ap:
            existing_group = new_ap.get("apGroupName", "unknown")
            pair.warnings.append(f"New AP {mapping.new_serial} already in venue (group: {existing_group})")

        pairs.append(pair)

    total_valid = sum(1 for p in pairs if p.valid)
    total_invalid = sum(1 for p in pairs if not p.valid)

    return PopSwapPreviewResponse(
        pairs=pairs,
        total_valid=total_valid,
        total_invalid=total_invalid,
        warnings=warnings,
    )


@router.post("/{controller_id}/venue/{venue_id}/apply")
async def apply_swap(
    controller_id: int,
    venue_id: str,
    request: PopSwapApplyRequest,
    background_tasks: BackgroundTasks,
    r1_client: R1Client = Depends(get_dynamic_r1_client),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Execute a Pop and Swap operation.

    1. Snapshots all old AP configs
    2. Provisions new APs (if needed) and assigns to venue/group
    3. Stores swap records in Redis for background config sync

    Returns a job_id-like response with swap_ids for tracking.
    """
    tenant_id = r1_client.tenant_id
    venues_service = r1_client.venues

    # Fetch all APs in venue for lookup
    all_aps = await venues_service.get_aps_by_tenant_venue(tenant_id, venue_id)
    if isinstance(all_aps, dict) and "data" in all_aps:
        ap_list = all_aps["data"]
    elif isinstance(all_aps, list):
        ap_list = all_aps
    else:
        ap_list = []
    ap_lookup = {ap.get("serialNumber", ""): ap for ap in ap_list if ap.get("serialNumber")}

    redis = await get_redis_client()
    store = SwapStore(redis)

    swap_ids = []
    results = []

    for mapping in request.mappings:
        old_ap = ap_lookup.get(mapping.old_serial)
        if not old_ap:
            results.append({
                "old_serial": mapping.old_serial,
                "new_serial": mapping.new_serial,
                "status": "error",
                "message": f"Old AP {mapping.old_serial} not found in venue",
            })
            continue

        try:
            # Phase 1: Snapshot old AP config
            snapshot = await capture_ap_snapshot(
                venues_service, tenant_id, venue_id, mapping.old_serial, ap_info=old_ap
            )

            # Phase 2: Provision + assign new AP
            ap_group_id = old_ap.get("apGroupId", "")
            ap_name = old_ap.get("name", "") if request.options.copy_name else ""

            # Try to assign new AP to venue + group
            try:
                if ap_group_id:
                    await venues_service.assign_ap_to_group(
                        tenant_id, venue_id, ap_group_id, mapping.new_serial,
                        wait_for_completion=True,
                    )
                if ap_name:
                    await venues_service.update_ap(
                        tenant_id, venue_id, mapping.new_serial,
                        name=ap_name,
                        wait_for_completion=True,
                    )
            except Exception as e:
                logger.warning(f"Assign/rename for {mapping.new_serial} had issues: {e}")
                # Continue anyway — the AP may need provisioning first

            # Phase 3: Store swap record
            swap_id = await store.create_swap(
                company_id=current_user.company_id,
                controller_id=controller_id,
                tenant_id=tenant_id,
                venue_id=venue_id,
                old_serial=mapping.old_serial,
                new_serial=mapping.new_serial,
                ap_name=snapshot.get("ap_name", ""),
                ap_group_id=snapshot.get("ap_group_id", ""),
                ap_group_name=snapshot.get("ap_group_name", ""),
                config_data=snapshot,
                created_by=current_user.id,
                cleanup_action=request.options.cleanup_action.value,
            )

            swap_ids.append(swap_id)
            results.append({
                "old_serial": mapping.old_serial,
                "new_serial": mapping.new_serial,
                "swap_id": swap_id,
                "status": "created",
                "settings_captured": len(snapshot.get("captured_settings", [])),
                "settings_failed": len(snapshot.get("failed_settings", [])),
            })

        except Exception as e:
            logger.error(f"Error processing swap {mapping.old_serial} -> {mapping.new_serial}: {e}")
            results.append({
                "old_serial": mapping.old_serial,
                "new_serial": mapping.new_serial,
                "status": "error",
                "message": str(e)[:500],
            })

    return {
        "swap_ids": swap_ids,
        "results": results,
        "total_created": len(swap_ids),
        "total_errors": len(results) - len(swap_ids),
    }


# ============================================================================
# Swap Management Endpoints
# ============================================================================

@router.get("/swaps", response_model=list[SwapRecordSummary])
async def list_swaps(
    controller_id: Optional[int] = Query(None, description="Filter by controller"),
    status: Optional[str] = Query(None, description="Filter by status"),
    current_user: User = Depends(get_current_user),
):
    """List swap records, scoped to company (super admins see all)."""
    redis = await get_redis_client()
    store = SwapStore(redis)

    if current_user.role == RoleEnum.super:
        swaps = await store.list_all_swaps()
    else:
        swaps = await store.list_swaps_for_company(current_user.company_id)

    # Apply filters
    if controller_id is not None:
        swaps = [s for s in swaps if s.get("controller_id") == controller_id]
    if status is not None:
        swaps = [s for s in swaps if s.get("status") == status]

    return [_to_summary(s) for s in swaps]


@router.get("/swaps/{swap_id}", response_model=SwapRecordDetail)
async def get_swap_detail(
    swap_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get full swap record detail including config snapshot and apply results."""
    redis = await get_redis_client()
    store = SwapStore(redis)

    swap = await store.get_swap(swap_id)
    if not swap:
        raise HTTPException(status_code=404, detail="Swap record not found")

    _check_company_access(swap, current_user)

    summary = _to_summary(swap)
    return SwapRecordDetail(
        **summary.model_dump(),
        config_data=swap.get("config_data"),
        apply_results=swap.get("apply_results") if isinstance(swap.get("apply_results"), dict) else None,
    )


@router.post("/swaps/{swap_id}/sync-now", response_model=SyncNowResponse)
async def sync_now(
    swap_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Immediately attempt config sync for a swap record."""
    redis = await get_redis_client()
    store = SwapStore(redis)

    swap = await store.get_swap(swap_id)
    if not swap:
        raise HTTPException(status_code=404, detail="Swap record not found")

    _check_company_access(swap, current_user)

    if swap.get("status") == "completed":
        return SyncNowResponse(swap_id=swap_id, status="completed", message="Already completed")
    if swap.get("status") == "syncing":
        return SyncNowResponse(swap_id=swap_id, status="syncing", message="Sync already in progress")

    # Get R1 client
    controller_id = swap["controller_id"]
    r1_client = create_r1_client_from_controller(controller_id, db)
    venues_service = r1_client.venues

    tenant_id = swap.get("tenant_id", "")
    venue_id = swap.get("venue_id", "")
    new_serial = swap.get("new_serial", "")

    # Check if AP is online
    try:
        ap = await venues_service.get_ap_by_tenant_venue_serial(tenant_id, venue_id, new_serial)
        if not ap:
            await store.increment_sync_attempts(swap_id)
            return SyncNowResponse(
                swap_id=swap_id, status="pending",
                message=f"New AP {new_serial} not found in venue yet",
            )

        ap_status = ap.get("status", "").lower()
        if ap_status != "online":
            await store.increment_sync_attempts(swap_id)
            return SyncNowResponse(
                swap_id=swap_id, status="pending",
                message=f"New AP {new_serial} is {ap_status}, not online yet",
            )
    except Exception as e:
        return SyncNowResponse(
            swap_id=swap_id, status="error",
            message=f"Error checking AP status: {str(e)[:200]}",
        )

    # AP is online — apply config
    await store.update_status(swap_id, "syncing")
    config_data = swap.get("config_data", {})

    try:
        result = await apply_config_to_ap(
            venues_service, tenant_id, venue_id, new_serial, config_data,
        )

        if result["success"]:
            await store.mark_completed(swap_id, result["results"])
            return SyncNowResponse(
                swap_id=swap_id, status="completed",
                message=f"Config applied: {result['applied']} settings applied, {result['failed']} failed",
                apply_results=result["results"],
            )
        else:
            await store.mark_failed(swap_id, result["results"])
            return SyncNowResponse(
                swap_id=swap_id, status="failed",
                message=f"Config sync had failures: {result['applied']} applied, {result['failed']} failed",
                apply_results=result["results"],
            )

    except Exception as e:
        await store.mark_failed(swap_id, {"error": str(e)[:500]})
        return SyncNowResponse(
            swap_id=swap_id, status="error",
            message=f"Config sync error: {str(e)[:200]}",
        )


@router.post("/swaps/{swap_id}/extend", response_model=ExtendResponse)
async def extend_swap(
    swap_id: str,
    current_user: User = Depends(get_current_user),
):
    """Extend migration window by 3 days (capped at 7 days max from now)."""
    redis = await get_redis_client()
    store = SwapStore(redis)

    swap = await store.get_swap(swap_id)
    if not swap:
        raise HTTPException(status_code=404, detail="Swap record not found")

    _check_company_access(swap, current_user)

    if swap.get("status") in ("completed", "expired"):
        raise HTTPException(status_code=400, detail=f"Cannot extend a {swap['status']} swap")

    new_expires = await store.extend_window(swap_id)
    if not new_expires:
        raise HTTPException(status_code=500, detail="Failed to extend window")

    return ExtendResponse(
        swap_id=swap_id,
        new_expires_at=new_expires,
        message=f"Migration window extended to {new_expires}",
    )


@router.delete("/swaps/{swap_id}")
async def cancel_swap(
    swap_id: str,
    current_user: User = Depends(get_current_user),
):
    """Cancel a pending swap and delete the record."""
    redis = await get_redis_client()
    store = SwapStore(redis)

    swap = await store.get_swap(swap_id)
    if not swap:
        raise HTTPException(status_code=404, detail="Swap record not found")

    _check_company_access(swap, current_user)

    if swap.get("status") == "syncing":
        raise HTTPException(status_code=400, detail="Cannot cancel a swap that is currently syncing")

    deleted = await store.delete_swap(swap_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete swap record")

    return {"message": "Swap record cancelled and deleted", "swap_id": swap_id}


# ============================================================================
# Helpers
# ============================================================================

def _check_company_access(swap: dict, user: User):
    """Enforce company-scoped access."""
    if user.role == RoleEnum.super:
        return
    swap_company = swap.get("company_id")
    if swap_company and swap_company != user.company_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this swap record")


def _to_summary(swap: dict) -> SwapRecordSummary:
    """Convert a raw swap dict to a SwapRecordSummary."""
    return SwapRecordSummary(
        swap_id=swap.get("swap_id", ""),
        controller_id=swap.get("controller_id", 0),
        venue_id=swap.get("venue_id", ""),
        old_serial=swap.get("old_serial", ""),
        new_serial=swap.get("new_serial", ""),
        ap_name=swap.get("ap_name"),
        ap_group_id=swap.get("ap_group_id"),
        ap_group_name=swap.get("ap_group_name"),
        status=swap.get("status", "unknown"),
        created_at=swap.get("created_at", ""),
        expires_at=swap.get("expires_at", ""),
        sync_attempts=swap.get("sync_attempts", 0),
        last_attempt_at=swap.get("last_attempt_at") or None,
        applied_at=swap.get("applied_at") or None,
        cleanup_action=swap.get("cleanup_action", "none"),
    )
