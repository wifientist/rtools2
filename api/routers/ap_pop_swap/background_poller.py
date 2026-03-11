"""
Background poller for Pop and Swap — runs every 30 minutes.

Checks all pending/failed swap records for new AP online status,
applies config when AP is online, marks expired records.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from redis_client import get_redis_client
from database import SessionLocal
from models.controller import Controller
from clients.r1_client import create_r1_client_from_controller

from .swap_store import SwapStore
from .config_sync import apply_config_to_ap

logger = logging.getLogger(__name__)

JOB_ID = "pop_swap_poller"


async def ensure_registered(scheduler) -> None:
    """Register the Pop and Swap poller job if it doesn't already exist."""
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        logger.info(f"Pop and Swap poller '{JOB_ID}' already registered")
        return

    await scheduler.register_job(
        job_id=JOB_ID,
        name="Pop and Swap Config Poller",
        callable_path="routers.ap_pop_swap.background_poller:poll_pending_swaps",
        trigger_type="interval",
        trigger_config={"minutes": 30},
        owner_type="system",
        description="Poll pending AP swaps every 30 minutes, apply config when new AP comes online",
    )
    logger.info(f"Registered Pop and Swap poller job '{JOB_ID}'")


async def poll_pending_swaps() -> Dict[str, Any]:
    """
    Main poller entry point — called by the scheduler every 30 minutes.

    Iterates all active swap records, checks if new APs are online,
    applies config where possible, and expires old records.
    """
    redis = await get_redis_client()
    store = SwapStore(redis)

    active_swaps = await store.list_active_swaps()
    if not active_swaps:
        return {"status": "success", "message": "No active swaps to process"}

    logger.info(f"Pop and Swap poller: processing {len(active_swaps)} active swaps")

    stats = {
        "processed": 0,
        "synced": 0,
        "still_pending": 0,
        "expired": 0,
        "errors": 0,
    }

    now = datetime.now(timezone.utc)

    for swap in active_swaps:
        swap_id = swap["swap_id"]
        status = swap.get("status", "")

        # Skip if already syncing or completed (shouldn't be in active set but safety check)
        if status in ("completed", "syncing"):
            continue

        # Check expiration
        expires_at_str = swap.get("expires_at", "")
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str)
                if now > expires_at:
                    logger.info(f"Swap {swap_id} expired, marking as expired")
                    await store.mark_expired(swap_id)
                    stats["expired"] += 1
                    continue
            except ValueError:
                pass

        stats["processed"] += 1

        try:
            result = await _try_sync_swap(store, swap)
            if result == "synced":
                stats["synced"] += 1
            elif result == "offline":
                stats["still_pending"] += 1
            else:
                stats["errors"] += 1
        except Exception as e:
            logger.error(f"Error processing swap {swap_id}: {e}")
            stats["errors"] += 1

    logger.info(f"Pop and Swap poller complete: {stats}")
    return {"status": "success", **stats}


async def _try_sync_swap(store: SwapStore, swap: dict) -> str:
    """
    Try to sync a single swap record.

    Returns: "synced", "offline", or "error"
    """
    swap_id = swap["swap_id"]
    controller_id = swap["controller_id"]
    tenant_id = swap.get("tenant_id", "")
    venue_id = swap.get("venue_id", "")
    new_serial = swap.get("new_serial", "")

    # Increment attempt counter
    await store.increment_sync_attempts(swap_id)

    # Get R1 client for this controller
    db = SessionLocal()
    try:
        r1_client = create_r1_client_from_controller(controller_id, db)
    except Exception as e:
        logger.error(f"Swap {swap_id}: Failed to create R1 client for controller {controller_id}: {e}")
        return "error"
    finally:
        db.close()

    venues_service = r1_client.venues

    # Check if new AP is online
    try:
        ap = await venues_service.get_ap_by_tenant_venue_serial(tenant_id, venue_id, new_serial)
        if not ap:
            logger.debug(f"Swap {swap_id}: New AP {new_serial} not found in venue")
            return "offline"

        ap_status = ap.get("status", "").lower()
        if ap_status != "online":
            logger.debug(f"Swap {swap_id}: New AP {new_serial} is {ap_status}, waiting...")
            return "offline"
    except Exception as e:
        logger.debug(f"Swap {swap_id}: Error checking AP status: {e}")
        return "offline"

    # AP is online — apply config
    logger.info(f"Swap {swap_id}: New AP {new_serial} is online, applying config...")
    await store.update_status(swap_id, "syncing")

    config_data = swap.get("config_data", {})
    if not config_data:
        logger.warning(f"Swap {swap_id}: No config data to apply")
        await store.mark_completed(swap_id, {"error": "no config data"})
        return "synced"

    try:
        result = await apply_config_to_ap(
            venues_service, tenant_id, venue_id, new_serial, config_data
        )

        if result["success"]:
            await store.mark_completed(swap_id, result["results"])
            logger.info(f"Swap {swap_id}: Config sync complete ({result['applied']} applied, {result['failed']} failed)")

            # Handle old AP cleanup
            await _cleanup_old_ap(swap, venues_service, tenant_id, venue_id)

            return "synced"
        else:
            await store.mark_failed(swap_id, result["results"])
            logger.warning(f"Swap {swap_id}: Config sync failed ({result['applied']} applied, {result['failed']} failed)")
            return "error"

    except Exception as e:
        logger.error(f"Swap {swap_id}: Config sync error: {e}")
        await store.mark_failed(swap_id, {"error": str(e)[:500]})
        return "error"


async def _cleanup_old_ap(swap: dict, venues_service, tenant_id: str, venue_id: str):
    """Handle old AP cleanup after successful config sync."""
    cleanup_action = swap.get("cleanup_action", "none")
    old_serial = swap.get("old_serial", "")

    if cleanup_action == "none" or not old_serial:
        return

    try:
        if cleanup_action == "unassign":
            # Remove old AP from its AP group by assigning to no group
            # R1 doesn't have a direct "unassign" — we'd need to move it to default group
            # For now, log it as a manual step
            logger.info(f"Swap {swap['swap_id']}: Old AP {old_serial} unassign requested (manual step)")
        elif cleanup_action == "remove":
            # Delete old AP from venue
            logger.info(f"Swap {swap['swap_id']}: Removing old AP {old_serial} from venue")
            await venues_service.delete_ap(tenant_id, venue_id, old_serial)
    except Exception as e:
        logger.warning(f"Swap {swap['swap_id']}: Old AP cleanup failed: {e}")
