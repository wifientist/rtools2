"""
DPSK Orchestrator Webhook Router.

This router receives webhooks from RuckusONE when DPSK-related activities occur,
triggering targeted incremental syncs for only the affected passphrases.

Key design: Webhooks do NOT trigger full pool syncs. Instead, they:
1. Parse the activity to identify affected passphrases
2. Fetch only those specific passphrases from the source pool
3. Add/update/flag just those passphrases in the site-wide pool

The webhook URL is universal: /api/orchestrator/webhook
Orchestrator identification is done via the webhook secret in the header.

**Activity tracking approach**: When a webhook arrives with an activity_id, we track
the activity via /activities/{id} polling ourselves rather than relying on webhook
status. This handles R1's quirk where entityId changes between IN_PROGRESS and SUCCESS.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import threading

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from starlette.requests import ClientDisconnect
from sqlalchemy.orm import Session

from dependencies import get_db
from models.controller import Controller
from redis_client import get_redis_client
from models.orchestrator import DPSKOrchestrator, OrchestratorSourcePool, PassphraseMapping, OrchestratorSyncEvent
from routers.orchestrator.sync_engine import SyncEngine
from routers.orchestrator.sync_pool import (
    sync_single_pool,
    sync_pool_by_id,
    add_passphrase_to_sitewide,
    PoolSyncResult
)
from clients.r1_client import create_r1_client_from_controller

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestrator/webhook", tags=["Orchestrator Webhooks"])

# Header names RuckusONE may use to send the webhook secret
# RuckusONE actually uses "Authorization" header, not "X-Webhook-Secret"
WEBHOOK_SECRET_HEADERS = ["Authorization", "X-Webhook-Secret"]


# ========== Activity Tracker ==========
# Tracks which activity_ids we're already monitoring to avoid duplicate processing.
# When a webhook arrives, we poll /activities/{id} ourselves to determine success
# and extract entity info, rather than relying on webhook status/entityId.

_tracked_activities: Set[str] = set()  # activity_ids currently being monitored
_activity_lock = threading.Lock()
_ACTIVITY_TTL_MINUTES = 30  # How long to keep tracking an activity

# Redis key prefix for webhook pause tracking
WEBHOOK_PAUSE_KEY_PREFIX = "webhook_pause:"
WEBHOOK_PAUSE_TTL_SECONDS = 3600  # 1 hour max pause (safety fallback)

# Simple in-memory cache for orchestrator lookups (avoids DB during webhook floods)
_orchestrator_cache: Dict[str, tuple] = {}  # secret -> (orchestrator_id, name, enabled, timestamp)
_CACHE_TTL_SECONDS = 60  # Cache for 1 minute


def is_activity_tracked(activity_id: str) -> bool:
    """Check if we're already tracking this activity."""
    if not activity_id:
        return False
    with _activity_lock:
        return activity_id in _tracked_activities


def track_activity(activity_id: str) -> bool:
    """
    Start tracking an activity. Returns True if newly tracked, False if already tracked.
    """
    if not activity_id:
        return False
    with _activity_lock:
        if activity_id in _tracked_activities:
            return False
        _tracked_activities.add(activity_id)
        logger.debug(f"Started tracking activity {activity_id}")
        return True


def untrack_activity(activity_id: str) -> None:
    """Stop tracking an activity (after completion or timeout)."""
    if not activity_id:
        return
    with _activity_lock:
        _tracked_activities.discard(activity_id)
        logger.debug(f"Stopped tracking activity {activity_id}")


# ========== Webhook Pause Tracking ==========
# Allows workflows (Cloudpath import, per-unit DPSK) to temporarily pause
# webhook processing to avoid conflicts during bulk operations.

def pause_webhooks_for_orchestrator(orchestrator_id: int, reason: str = "bulk_import", ttl_seconds: int = None) -> bool:
    """
    Pause webhook processing for an orchestrator.

    Used by bulk import workflows to prevent webhook floods from causing conflicts.

    Args:
        orchestrator_id: ID of the orchestrator to pause
        reason: Reason for pausing (for logging)
        ttl_seconds: How long to pause (default: WEBHOOK_PAUSE_TTL_SECONDS)

    Returns:
        True if pause was set, False on error
    """
    try:
        redis = get_redis_client()
        key = f"{WEBHOOK_PAUSE_KEY_PREFIX}{orchestrator_id}"
        ttl = ttl_seconds or WEBHOOK_PAUSE_TTL_SECONDS
        redis.setex(key, ttl, reason)
        logger.info(f"Paused webhooks for orchestrator {orchestrator_id}: {reason} (TTL: {ttl}s)")
        return True
    except Exception as e:
        logger.error(f"Failed to pause webhooks for orchestrator {orchestrator_id}: {e}")
        return False


def resume_webhooks_for_orchestrator(orchestrator_id: int) -> bool:
    """
    Resume webhook processing for an orchestrator.

    Args:
        orchestrator_id: ID of the orchestrator to resume

    Returns:
        True if resumed (or wasn't paused), False on error
    """
    try:
        redis = get_redis_client()
        key = f"{WEBHOOK_PAUSE_KEY_PREFIX}{orchestrator_id}"
        redis.delete(key)
        logger.info(f"Resumed webhooks for orchestrator {orchestrator_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to resume webhooks for orchestrator {orchestrator_id}: {e}")
        return False


def is_webhook_paused(orchestrator_id: int) -> tuple[bool, Optional[str]]:
    """
    Check if webhook processing is paused for an orchestrator.

    Args:
        orchestrator_id: ID of the orchestrator to check

    Returns:
        Tuple of (is_paused, reason_if_paused)
    """
    try:
        redis = get_redis_client()
        key = f"{WEBHOOK_PAUSE_KEY_PREFIX}{orchestrator_id}"
        reason = redis.get(key)
        if reason:
            return True, reason.decode('utf-8') if isinstance(reason, bytes) else reason
        return False, None
    except Exception as e:
        logger.error(f"Failed to check webhook pause for orchestrator {orchestrator_id}: {e}")
        return False, None  # Fail open - don't block webhooks on Redis errors


class CachedOrchestrator:
    """Minimal orchestrator info for cache-based early exit."""
    def __init__(self, id: int, name: str, enabled: bool):
        self.id = id
        self.name = name
        self.enabled = enabled


def find_orchestrator_by_secret_cached(secret: str) -> Optional[CachedOrchestrator]:
    """
    Check cache for orchestrator by secret (no DB hit).

    Returns cached minimal info if available and fresh, None otherwise.
    Used for quick early exit when orchestrator is disabled.
    """
    import time

    if not secret:
        return None

    cached = _orchestrator_cache.get(secret)
    if cached:
        orch_id, name, enabled, timestamp = cached
        if time.time() - timestamp < _CACHE_TTL_SECONDS:
            return CachedOrchestrator(orch_id, name, enabled)
    return None


def invalidate_orchestrator_cache(secret: str = None, orchestrator_id: int = None) -> None:
    """
    Invalidate cached orchestrator data.

    Call this when orchestrator enabled/disabled status changes.

    Args:
        secret: Webhook secret to invalidate (if known)
        orchestrator_id: Orchestrator ID to invalidate (searches all cached entries)
    """
    if secret and secret in _orchestrator_cache:
        del _orchestrator_cache[secret]
        logger.debug(f"Invalidated orchestrator cache for secret")
        return

    if orchestrator_id:
        # Search for matching orchestrator_id
        to_delete = [s for s, (oid, _, _, _) in _orchestrator_cache.items() if oid == orchestrator_id]
        for s in to_delete:
            del _orchestrator_cache[s]
        if to_delete:
            logger.debug(f"Invalidated orchestrator cache for ID {orchestrator_id}")


def find_orchestrator_by_secret(db: Session, secret: str) -> Optional[DPSKOrchestrator]:
    """
    Find an orchestrator by its webhook secret.

    Updates the cache on lookup for future quick checks.

    Args:
        db: Database session
        secret: The secret from the webhook header

    Returns:
        Matching orchestrator or None if not found
    """
    import time

    if not secret:
        return None

    # Find orchestrator with matching secret
    orchestrator = db.query(DPSKOrchestrator).filter(
        DPSKOrchestrator.webhook_secret == secret
    ).first()

    # Update cache
    if orchestrator:
        _orchestrator_cache[secret] = (orchestrator.id, orchestrator.name, orchestrator.enabled, time.time())
    elif secret in _orchestrator_cache:
        del _orchestrator_cache[secret]

    return orchestrator


def normalize_uuid(uuid_str: str) -> str:
    """Normalize UUID by removing hyphens and lowercasing for comparison."""
    if not uuid_str:
        return ""
    return uuid_str.replace("-", "").lower()


def find_matching_source_pool(orchestrator: DPSKOrchestrator, entity_id: str):
    """
    Find a source pool that matches the entity_id.
    Handles UUID format differences (with/without hyphens).
    """
    entity_normalized = normalize_uuid(entity_id)

    for pool in orchestrator.source_pools:
        # Check pool_id match
        if normalize_uuid(pool.pool_id) == entity_normalized:
            return pool
        # Check identity_group_id match
        if pool.identity_group_id and normalize_uuid(pool.identity_group_id) == entity_normalized:
            return pool

    return None


async def track_and_process_activity(
    orchestrator_id: int,
    activity_id: str,
    use_case: str,
    initial_entity_id: str = None
):
    """
    Track an activity via /activities polling and trigger sync on completion.

    This is the main entry point for webhook processing. Instead of relying on
    webhook status (which can be unreliable), we poll the activity endpoint
    ourselves to determine success and extract entity info.

    Args:
        orchestrator_id: ID of the orchestrator
        activity_id: RuckusONE activity ID to track
        use_case: The webhook useCase (BULK_CREATE_PERSONAS, etc.)
        initial_entity_id: Entity ID from webhook (used as fallback)
    """
    from database import SessionLocal
    db = SessionLocal()

    try:
        # Check if already tracking this activity
        if not track_activity(activity_id):
            logger.debug(f"Activity {activity_id} already being tracked, skipping")
            return

        orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
        if not orchestrator or not orchestrator.enabled:
            logger.warning(f"Orchestrator {orchestrator_id} not found or disabled")
            return

        # Get R1 client for polling
        controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
        if not controller:
            logger.error(f"Controller not found for orchestrator {orchestrator_id}")
            return

        r1_client = create_r1_client_from_controller(controller.id, db)

        # Poll activity until completion
        logger.info(f"Tracking activity {activity_id} for orchestrator {orchestrator.name}")
        try:
            activity_data = await r1_client.await_task_completion(
                request_id=activity_id,
                override_tenant_id=orchestrator.tenant_id,
                max_attempts=60  # ~100s with stepped backoff
            )
        except TimeoutError:
            logger.warning(f"Activity {activity_id} timed out")
            return
        except Exception as e:
            logger.error(f"Activity {activity_id} failed: {e}")
            return

        # Extract entity info from activity response
        # Activity response should contain the actual pool/identity group ID
        entity_id = _extract_entity_from_activity(activity_data, initial_entity_id)

        if not entity_id:
            logger.warning(f"Could not determine entity_id from activity {activity_id}")
            return

        logger.info(f"Activity {activity_id} completed. Entity: {entity_id}")

        # Dispatch to appropriate handler based on use_case
        create_cases = ["BULK_CREATE_PERSONAS", "CREATE_PERSONA"]
        update_cases = ["UPDATE_PERSONA"]
        delete_cases = ["DELETE_PERSONA", "BULK_DELETE_PERSONAS"]

        # Close db session before calling handlers (they create their own)
        db.close()
        db = None

        if use_case in create_cases:
            await process_webhook_create(
                orchestrator_id=orchestrator_id,
                activity_id=activity_id,
                entity_id=entity_id,
                webhook_payload={"activity_data": activity_data}
            )
        elif use_case in update_cases:
            await process_webhook_update(
                orchestrator_id=orchestrator_id,
                activity_id=activity_id,
                entity_id=entity_id,
                webhook_payload={"activity_data": activity_data}
            )
        elif use_case in delete_cases:
            await process_webhook_delete(
                orchestrator_id=orchestrator_id,
                activity_id=activity_id,
                entity_id=entity_id,
                webhook_payload={"activity_data": activity_data}
            )

    except Exception as e:
        logger.error(f"Error tracking activity {activity_id}: {e}")
    finally:
        untrack_activity(activity_id)
        if db:
            db.close()


def _extract_entity_from_activity(activity_data: dict, fallback_entity_id: str = None) -> Optional[str]:
    """
    Extract the entity ID (pool or identity group) from activity response.

    R1 activity responses contain entity info in various fields depending on the operation.
    This function checks common locations.

    Args:
        activity_data: Response from /activities/{id}
        fallback_entity_id: Entity ID from webhook to use as fallback

    Returns:
        Entity ID if found, None otherwise
    """
    if not activity_data:
        return fallback_entity_id

    # Check common locations for entity info
    # 1. Direct entityId field
    entity_id = activity_data.get('entityId')
    if entity_id:
        return entity_id

    # 2. Check 'data' object
    data = activity_data.get('data', {})
    if isinstance(data, dict):
        entity_id = data.get('dpskPoolId') or data.get('identityGroupId') or data.get('entityId')
        if entity_id:
            return entity_id

    # 3. Check 'results' array (bulk operations)
    results = activity_data.get('results', [])
    if results and isinstance(results, list) and len(results) > 0:
        first_result = results[0]
        if isinstance(first_result, dict):
            entity_id = first_result.get('dpskPoolId') or first_result.get('identityGroupId')
            if entity_id:
                return entity_id

    # 4. Check 'steps' array
    steps = activity_data.get('steps', [])
    if steps and isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                entity_id = step.get('entityId') or step.get('dpskPoolId')
                if entity_id:
                    return entity_id

    # 5. Use fallback from webhook
    return fallback_entity_id


async def process_webhook_create(
    orchestrator_id: int,
    activity_id: str,
    entity_id: str,
    webhook_payload: dict
):
    """
    Process a CREATE/BULK_CREATE webhook - add specific passphrases to site-wide.

    Uses the unified sync_pool_by_id() function which handles:
    - Source pool lookup (with UUID normalization)
    - Sync event creation
    - Passphrase fetching and creation
    - Identity updates
    - Mapping records

    Args:
        orchestrator_id: ID of the orchestrator
        activity_id: RuckusONE activity ID
        entity_id: Entity ID (pool or identity group)
        webhook_payload: Full webhook payload for extracting details
    """
    from database import SessionLocal
    db = SessionLocal()

    try:
        orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
        if not orchestrator or not orchestrator.enabled:
            logger.warning(f"Orchestrator {orchestrator_id} not found or disabled")
            return

        # Log configured pools for debugging
        logger.info(f"Webhook entity_id: {entity_id}")
        logger.info(f"Site-wide pool: {orchestrator.site_wide_pool_id}")
        logger.info(f"Source pools: {[(p.pool_id, p.pool_name, p.identity_group_id) for p in orchestrator.source_pools]}")

        # Check if this is the site-wide pool (destination) - flag as manual entry
        if normalize_uuid(entity_id) == normalize_uuid(orchestrator.site_wide_pool_id):
            logger.warning(f"Passphrase created directly in site-wide pool! This should be flagged.")
            # TODO: Create a flagged mapping entry for manual review
            # For now, just log and return - we don't want to sync site-wide to itself
            return

        # Get R1 client
        controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
        if not controller:
            logger.error(f"Controller not found for orchestrator {orchestrator_id}")
            return

        r1_client = create_r1_client_from_controller(controller.id, db)

        logger.info(f"Processing CREATE webhook for entity {entity_id}")

        # Try to get specific passphrase IDs from activity details
        passphrase_ids = await _extract_passphrase_ids_from_activity(
            r1_client, activity_id, orchestrator.tenant_id
        )

        if passphrase_ids:
            logger.info(f"Activity provided {len(passphrase_ids)} passphrase IDs")
        else:
            logger.info("No passphrase IDs in activity - will scan for new passphrases")

        # Use unified sync function - handles everything
        try:
            result = await sync_pool_by_id(
                db=db,
                r1_client=r1_client,
                orchestrator=orchestrator,
                pool_id=entity_id,
                specific_passphrase_ids=passphrase_ids if passphrase_ids else None,
                create_sync_event=True,
                event_type="webhook"
            )

            logger.info(
                f"Webhook CREATE processed for {result.pool_name}: "
                f"+{result.added} added, ~{result.updated} updated, ={result.skipped} unchanged"
            )

        except ValueError as e:
            # Pool not found in source pools
            logger.warning(str(e))
            return

    except Exception as e:
        logger.error(f"Webhook CREATE processing failed: {e}")
    finally:
        db.close()


async def process_webhook_delete(
    orchestrator_id: int,
    activity_id: str,
    entity_id: str,
    webhook_payload: dict
):
    """
    Process a DELETE/BULK_DELETE webhook - flag affected mappings.

    Uses the unified sync_pool_by_id() function which handles deletion
    detection by comparing current source pool state against our mappings.

    Args:
        orchestrator_id: ID of the orchestrator
        activity_id: RuckusONE activity ID
        entity_id: Entity ID (pool or identity group)
        webhook_payload: Full webhook payload for extracting details
    """
    from database import SessionLocal
    db = SessionLocal()

    try:
        orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
        if not orchestrator or not orchestrator.enabled:
            logger.warning(f"Orchestrator {orchestrator_id} not found or disabled")
            return

        # Check if this is the site-wide pool (destination) - deletion there is handled differently
        if normalize_uuid(entity_id) == normalize_uuid(orchestrator.site_wide_pool_id):
            logger.info(f"Passphrase deleted from site-wide pool - no action needed (manual deletion)")
            return

        # Get R1 client
        controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
        if not controller:
            logger.error(f"Controller not found for orchestrator {orchestrator_id}")
            return

        r1_client = create_r1_client_from_controller(controller.id, db)

        logger.info(f"Processing DELETE webhook for entity {entity_id}")

        # For delete webhooks, we do a full pool scan to detect what's missing
        # This is more reliable than trying to extract specific IDs from activity
        # (which may already be deleted from R1)
        try:
            result = await sync_pool_by_id(
                db=db,
                r1_client=r1_client,
                orchestrator=orchestrator,
                pool_id=entity_id,
                specific_passphrase_ids=None,  # Full scan to detect deletions
                create_sync_event=True,
                event_type="webhook"
            )

            logger.info(
                f"Webhook DELETE processed for {result.pool_name}: "
                f"-{result.flagged} flagged for removal"
            )

        except ValueError as e:
            # Pool not found in source pools
            logger.warning(str(e))
            return

    except Exception as e:
        logger.error(f"Webhook DELETE processing failed: {e}")
    finally:
        db.close()


async def process_webhook_update(
    orchestrator_id: int,
    activity_id: str,
    entity_id: str,
    webhook_payload: dict
):
    """
    Process an UPDATE webhook - update specific passphrase in site-wide.

    Uses the unified sync_pool_by_id() function which handles update detection
    by comparing source passphrase state against our synced mappings.

    Args:
        orchestrator_id: ID of the orchestrator
        activity_id: RuckusONE activity ID
        entity_id: Entity ID (pool or identity group)
        webhook_payload: Full webhook payload for extracting details
    """
    from database import SessionLocal
    db = SessionLocal()

    try:
        orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
        if not orchestrator or not orchestrator.enabled:
            return

        # Check if this is the site-wide pool (destination) - updates there are handled differently
        if normalize_uuid(entity_id) == normalize_uuid(orchestrator.site_wide_pool_id):
            logger.info(f"Passphrase updated in site-wide pool - no action needed (manual update)")
            return

        # Get R1 client
        controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
        if not controller:
            logger.error(f"Controller not found for orchestrator {orchestrator_id}")
            return

        r1_client = create_r1_client_from_controller(controller.id, db)

        logger.info(f"Processing UPDATE webhook for entity {entity_id}")

        # Try to get specific passphrase IDs from activity
        passphrase_ids = await _extract_passphrase_ids_from_activity(
            r1_client, activity_id, orchestrator.tenant_id
        )

        if passphrase_ids:
            logger.info(f"Activity provided {len(passphrase_ids)} passphrase IDs for update")
        else:
            logger.info("No passphrase IDs in activity - will scan for changes")

        # Use unified sync function - it handles update detection
        try:
            result = await sync_pool_by_id(
                db=db,
                r1_client=r1_client,
                orchestrator=orchestrator,
                pool_id=entity_id,
                specific_passphrase_ids=passphrase_ids if passphrase_ids else None,
                create_sync_event=True,
                event_type="webhook"
            )

            logger.info(
                f"Webhook UPDATE processed for {result.pool_name}: "
                f"~{result.updated} updated, +{result.added} added"
            )

        except ValueError as e:
            # Pool not found in source pools
            logger.warning(str(e))
            return

    except Exception as e:
        logger.error(f"Webhook UPDATE processing failed: {e}")
    finally:
        db.close()


# ========== Helper Functions ==========

async def _extract_passphrase_ids_from_activity(
    r1_client,
    activity_id: str,
    tenant_id: str
) -> List[str]:
    """
    Try to extract passphrase IDs from an activity's details.

    RuckusONE activities sometimes include the IDs of affected resources
    in their response data.

    Returns:
        List of passphrase IDs, or empty list if not available
    """
    if not activity_id:
        return []

    try:
        # Fetch activity details
        if r1_client.ec_type == "MSP" and tenant_id:
            response = r1_client.get(f"/activities/{activity_id}", override_tenant_id=tenant_id)
        else:
            response = r1_client.get(f"/activities/{activity_id}")

        if not response.ok:
            logger.debug(f"Could not fetch activity {activity_id}: {response.status_code}")
            return []

        data = response.json()

        # Look for passphrase IDs in various possible locations
        passphrase_ids = []

        # Check 'results' array (common for bulk operations)
        results = data.get('results', [])
        for result in results:
            if isinstance(result, dict):
                pp_id = result.get('id') or result.get('passphraseId') or result.get('entityId')
                if pp_id:
                    passphrase_ids.append(pp_id)

        # Check 'data' object (common for single operations)
        result_data = data.get('data', {})
        if isinstance(result_data, dict):
            pp_id = result_data.get('id') or result_data.get('passphraseId')
            if pp_id:
                passphrase_ids.append(pp_id)

        # Check 'entityIds' array
        entity_ids = data.get('entityIds', [])
        if entity_ids:
            passphrase_ids.extend(entity_ids)

        logger.debug(f"Extracted {len(passphrase_ids)} passphrase IDs from activity {activity_id}")
        return passphrase_ids

    except Exception as e:
        logger.warning(f"Error extracting passphrase IDs from activity {activity_id}: {e}")
        return []


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Receive webhook from RuckusONE activity notifications.

    This is a universal endpoint for all orchestrators. The orchestrator is identified
    by the webhook secret sent in the X-Webhook-Secret header.

    **Important**: A webhook secret MUST be configured for the orchestrator to receive
    webhooks. Configure this in the orchestrator settings.

    Expected headers:
    - X-Webhook-Secret: The secret configured for this orchestrator

    Expected payload structure:
    ```json
    {
        "id": "webhook-config-id",
        "name": "webhook-name",
        "type": "activity",
        "tenantId": "tenant-id",
        "payload": {
            "id": "activity-id",
            "requestId": "activity-id",
            "useCase": "BULK_CREATE_PERSONAS",
            "status": "SUCCESS",
            "entityId": "pool-or-group-id",
            ...
        }
    }
    ```
    """
    # 1. Get the webhook secret from header FIRST (before reading body)
    secret = None
    for header_name in WEBHOOK_SECRET_HEADERS:
        secret = request.headers.get(header_name)
        if secret:
            break

    if not secret:
        logger.warning(f"Webhook received without secret header. Available headers: {list(request.headers.keys())}")
        raise HTTPException(
            status_code=401,
            detail=f"Missing secret header. Expected one of: {WEBHOOK_SECRET_HEADERS}. Configure a webhook secret in the orchestrator settings."
        )

    # 2. CACHE CHECK: Quick early exit for disabled orchestrators (no DB hit)
    cached = find_orchestrator_by_secret_cached(secret)
    if cached and not cached.enabled:
        # Cached disabled orchestrator - skip DB lookup entirely
        logger.debug(f"Webhook for disabled orchestrator {cached.name} (cached) - skipping")
        return {"status": "acknowledged", "reason": "orchestrator_disabled"}

    if cached:
        # Cache hit for enabled - still check pause status before DB lookup
        paused, pause_reason = is_webhook_paused(cached.id)
        if paused:
            logger.debug(f"Webhook for paused orchestrator {cached.name} ({pause_reason}) - skipping")
            return {"status": "acknowledged", "reason": f"orchestrator_paused:{pause_reason}"}

    # 3. Find orchestrator by secret (DB lookup - also updates cache)
    orchestrator = find_orchestrator_by_secret(db, secret)
    if not orchestrator:
        logger.warning("Webhook received with unknown secret")
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook secret. No matching orchestrator found."
        )

    # 4. EARLY EXIT: Check enabled/paused BEFORE reading body (zero processing)
    # (Double-check in case cache was stale)
    if not orchestrator.enabled:
        logger.debug(f"Webhook for disabled orchestrator {orchestrator.name} - skipping body read")
        return {"status": "acknowledged", "reason": "orchestrator_disabled"}

    # Check pause status (skip if already checked via cache above)
    if not cached:
        paused, pause_reason = is_webhook_paused(orchestrator.id)
        if paused:
            logger.debug(f"Webhook for paused orchestrator {orchestrator.name} ({pause_reason}) - skipping body read")
            return {"status": "acknowledged", "reason": f"orchestrator_paused:{pause_reason}"}

    # 4. Read body once and parse JSON (only if enabled and not paused)
    try:
        body = await request.body()
        data = json.loads(body) if body else {}
    except ClientDisconnect:
        # Client closed connection before we could read body
        # This commonly happens during rapid webhook floods from RuckusONE
        # Just log and return - we can't respond anyway
        logger.debug(f"Webhook client disconnected before body read (orchestrator: {orchestrator.name})")
        return {"status": "client_disconnected"}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse webhook JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception as e:
        logger.error(f"Failed to read webhook body: {e!r}")  # Use !r for better exception repr
        raise HTTPException(status_code=400, detail="Failed to read request body")

    # Log webhook receipt
    logger.debug(f"Webhook for orchestrator {orchestrator.name}: {body[:200].decode('utf-8', errors='replace') if body else '(empty)'}...")

    # 5. Extract relevant fields
    payload = data.get("payload", {})
    webhook_type = data.get("type", "")
    use_case = payload.get("useCase", "")
    status = payload.get("status", "")
    entity_id = payload.get("entityId", "")
    activity_id = payload.get("requestId") or payload.get("id", "")

    logger.debug(f"Webhook payload: useCase={use_case}, entityId={entity_id}, activity_id={activity_id}")

    # 6. Handle test messages
    if webhook_type == "test":
        logger.info(f"Test webhook received for orchestrator {orchestrator.name}")
        return {
            "status": "ok",
            "message": "Test webhook received successfully",
            "orchestrator_id": orchestrator.id,
            "orchestrator_name": orchestrator.name
        }

    logger.info(f"Webhook for orchestrator {orchestrator.id}: useCase={use_case}, status={status}, entityId={entity_id}, activityId={activity_id}")

    # 7. Filter for relevant events
    create_cases = ["BULK_CREATE_PERSONAS", "CREATE_PERSONA"]
    update_cases = ["UPDATE_PERSONA"]
    delete_cases = ["DELETE_PERSONA", "BULK_DELETE_PERSONAS"]
    all_relevant_cases = create_cases + update_cases + delete_cases

    if use_case not in all_relevant_cases:
        logger.debug(f"Ignoring webhook with useCase: {use_case}")
        return {"status": "ignored", "reason": f"useCase '{use_case}' not relevant"}

    # 7a. Require activity_id for tracking
    if not activity_id:
        logger.warning("Webhook missing activity_id, cannot track")
        return {"status": "ignored", "reason": "No activity_id to track"}

    # Log active tracking count for debugging webhook floods
    with _activity_lock:
        active_count = len(_tracked_activities)
    if active_count > 10:
        logger.warning(f"High webhook load: {active_count} activities being tracked")

    # 7b. Check if already tracking this activity (multiple webhooks for same activity)
    if is_activity_tracked(activity_id):
        logger.info(f"Activity {activity_id} already being tracked, acknowledging duplicate webhook")
        return {
            "status": "acknowledged",
            "reason": "Activity already being tracked",
            "activity_id": activity_id
        }

    # 7c. Start activity tracking in background
    # We poll /activities/{id} ourselves to determine success and extract entity info.
    # This handles R1's quirk where entityId changes between IN_PROGRESS and SUCCESS.
    logger.info(f"Starting activity tracking for {activity_id} (useCase={use_case})")

    background_tasks.add_task(
        track_and_process_activity,
        orchestrator_id=orchestrator.id,
        activity_id=activity_id,
        use_case=use_case,
        initial_entity_id=entity_id  # Fallback if activity response lacks entity info
    )

    # Determine action for response
    if use_case in create_cases:
        action = "create"
    elif use_case in update_cases:
        action = "update"
    else:  # delete_cases
        action = "delete"

    return {
        "status": "accepted",
        "action": action,
        "orchestrator_id": orchestrator.id,
        "orchestrator_name": orchestrator.name,
        "activity_id": activity_id,
        "use_case": use_case,
        "note": "Tracking activity via /activities polling. Sync will trigger on completion."
    }


@router.get("/test")
async def test_webhook_endpoint():
    """
    Test endpoint to verify webhook URL is reachable.

    Use this to test connectivity from RuckusONE before configuring the actual webhook.
    """
    return {
        "status": "ok",
        "message": "Webhook endpoint is reachable",
        "expected_headers": WEBHOOK_SECRET_HEADERS,
        "instructions": "RuckusONE sends the secret in the 'Authorization' header when configuring webhooks"
    }


@router.post("/simulate/{orchestrator_id}")
async def simulate_webhook(
    orchestrator_id: int,
    action: str,  # "create", "delete", "update"
    pool_id: str,
    background_tasks: BackgroundTasks,
    passphrase_ids: Optional[str] = None,  # Comma-separated list of passphrase IDs
    db: Session = Depends(get_db)
):
    """
    Simulate a webhook for local testing.

    This bypasses the actual RuckusONE webhook and directly triggers
    the incremental sync handlers.

    Args:
        orchestrator_id: ID of the orchestrator to sync
        action: "create", "delete", or "update"
        pool_id: Source pool ID that was affected
        passphrase_ids: Optional comma-separated list of specific passphrase IDs

    Example:
        POST /api/orchestrator/webhook/simulate/2?action=create&pool_id=abc123
        POST /api/orchestrator/webhook/simulate/2?action=delete&pool_id=abc123&passphrase_ids=pp1,pp2
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    if not orchestrator.enabled:
        raise HTTPException(status_code=400, detail="Orchestrator is disabled")

    # Verify pool is a source pool
    source_pool = None
    for pool in orchestrator.source_pools:
        if pool.pool_id == pool_id:
            source_pool = pool
            break

    if not source_pool:
        raise HTTPException(
            status_code=400,
            detail=f"Pool {pool_id} is not a source pool for this orchestrator"
        )

    # Build simulated webhook payload
    use_case_map = {
        "create": "BULK_CREATE_PERSONAS",
        "delete": "BULK_DELETE_PERSONAS",
        "update": "UPDATE_PERSONA"
    }

    if action not in use_case_map:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action: {action}. Must be 'create', 'delete', or 'update'"
        )

    simulated_payload = {
        "type": "activity",
        "payload": {
            "useCase": use_case_map[action],
            "status": "SUCCESS",
            "entityId": pool_id,
            "requestId": f"simulated-{action}-{datetime.utcnow().isoformat()}"
        }
    }

    # If specific passphrase IDs provided, add them to payload
    if passphrase_ids:
        simulated_payload["payload"]["entityIds"] = passphrase_ids.split(",")

    logger.info(f"Simulating {action} webhook for orchestrator {orchestrator_id}, pool {pool_id}")

    # Dispatch to appropriate handler
    if action == "create":
        background_tasks.add_task(
            process_webhook_create,
            orchestrator_id=orchestrator.id,
            activity_id=simulated_payload["payload"]["requestId"],
            entity_id=pool_id,
            webhook_payload=simulated_payload
        )
    elif action == "update":
        background_tasks.add_task(
            process_webhook_update,
            orchestrator_id=orchestrator.id,
            activity_id=simulated_payload["payload"]["requestId"],
            entity_id=pool_id,
            webhook_payload=simulated_payload
        )
    elif action == "delete":
        background_tasks.add_task(
            process_webhook_delete,
            orchestrator_id=orchestrator.id,
            activity_id=simulated_payload["payload"]["requestId"],
            entity_id=pool_id,
            webhook_payload=simulated_payload
        )

    return {
        "status": "accepted",
        "action": action,
        "orchestrator_id": orchestrator.id,
        "orchestrator_name": orchestrator.name,
        "pool_id": pool_id,
        "pool_name": source_pool.pool_name,
        "note": "Simulated webhook processing in background"
    }


@router.post("/sync-pool/{orchestrator_id}")
async def sync_source_pool(
    orchestrator_id: int,
    pool_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Trigger incremental sync for a specific source pool.

    This is a standalone function that can be called directly (outside webhook flow)
    to scan a source pool for new passphrases and push them to site-wide.

    Use this when:
    - Webhooks missed a passphrase creation
    - You want to sync a pool without waiting for webhooks
    - Testing sync functionality

    Args:
        orchestrator_id: ID of the orchestrator
        pool_id: Source pool ID to sync

    Example:
        POST /api/orchestrator/webhook/sync-pool/2?pool_id=abc123
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    if not orchestrator.enabled:
        raise HTTPException(status_code=400, detail="Orchestrator is disabled")

    # Find source pool (handles UUID normalization)
    source_pool = find_matching_source_pool(orchestrator, pool_id)
    if not source_pool:
        # Also try direct match
        for pool in orchestrator.source_pools:
            if pool.pool_id == pool_id:
                source_pool = pool
                break

    if not source_pool:
        raise HTTPException(
            status_code=400,
            detail=f"Pool {pool_id} is not a source pool for this orchestrator"
        )

    logger.info(f"Starting incremental sync for pool {source_pool.pool_name} on orchestrator {orchestrator.name}")

    # Run sync in background using the unified sync function
    async def run_pool_sync():
        from database import SessionLocal
        sync_db = SessionLocal()
        try:
            orch = sync_db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
            controller = sync_db.query(Controller).filter_by(id=orch.controller_id).first()
            r1_client = create_r1_client_from_controller(controller.id, sync_db)

            result = await sync_pool_by_id(
                db=sync_db,
                r1_client=r1_client,
                orchestrator=orch,
                pool_id=pool_id,
                create_sync_event=True,
                event_type="manual"
            )
            logger.info(f"Manual sync complete: {result.pool_name} - +{result.added}, ~{result.updated}, -{result.flagged}")
        except Exception as e:
            logger.error(f"Manual pool sync failed: {e}")
        finally:
            sync_db.close()

    background_tasks.add_task(run_pool_sync)

    return {
        "status": "accepted",
        "orchestrator_id": orchestrator.id,
        "orchestrator_name": orchestrator.name,
        "pool_id": source_pool.pool_id,
        "pool_name": source_pool.pool_name,
        "note": "Incremental sync started. Scanning for new passphrases and pushing to site-wide."
    }


@router.post("/sync-all/{orchestrator_id}")
async def sync_all_source_pools(
    orchestrator_id: int,
    background_tasks: BackgroundTasks,
    parallel: bool = True,
    db: Session = Depends(get_db)
):
    """
    Trigger incremental sync for ALL source pools of an orchestrator.

    Scans each source pool for new passphrases and pushes them to site-wide.
    By default runs pools in parallel for faster execution.

    Args:
        orchestrator_id: ID of the orchestrator
        parallel: If True (default), sync pools concurrently. If False, sync sequentially.

    Example:
        POST /api/orchestrator/webhook/sync-all/2
        POST /api/orchestrator/webhook/sync-all/2?parallel=false
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    if not orchestrator.enabled:
        raise HTTPException(status_code=400, detail="Orchestrator is disabled")

    if not orchestrator.source_pools:
        raise HTTPException(status_code=400, detail="Orchestrator has no source pools configured")

    pool_count = len(orchestrator.source_pools)
    pool_info = [{"pool_id": p.pool_id, "pool_name": p.pool_name} for p in orchestrator.source_pools]

    logger.info(f"Starting {'parallel' if parallel else 'sequential'} sync for {pool_count} source pools on {orchestrator.name}")

    # Run all pool syncs in background
    async def run_all_pool_syncs():
        import asyncio
        from database import SessionLocal

        async def sync_one_pool(pool_id: str, pool_name: str) -> PoolSyncResult:
            """Sync a single pool with its own db session."""
            sync_db = SessionLocal()
            try:
                orch = sync_db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
                controller = sync_db.query(Controller).filter_by(id=orch.controller_id).first()
                r1_client = create_r1_client_from_controller(controller.id, sync_db)

                return await sync_pool_by_id(
                    db=sync_db,
                    r1_client=r1_client,
                    orchestrator=orch,
                    pool_id=pool_id,
                    create_sync_event=True,
                    event_type="manual"
                )
            except Exception as e:
                logger.error(f"Failed to sync pool {pool_name}: {e}")
                return PoolSyncResult(pool_id=pool_id, pool_name=pool_name, errors=[str(e)])
            finally:
                sync_db.close()

        if parallel:
            # Run all pools concurrently
            tasks = [sync_one_pool(p["pool_id"], p["pool_name"]) for p in pool_info]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            # Run pools sequentially
            results = []
            for p in pool_info:
                result = await sync_one_pool(p["pool_id"], p["pool_name"])
                results.append(result)

        # Summary logging
        total_added = sum(r.added for r in results if isinstance(r, PoolSyncResult))
        total_updated = sum(r.updated for r in results if isinstance(r, PoolSyncResult))
        total_flagged = sum(r.flagged for r in results if isinstance(r, PoolSyncResult))
        total_errors = sum(len(r.errors) for r in results if isinstance(r, PoolSyncResult))

        logger.info(
            f"Sync-all complete for {orchestrator.name}: "
            f"{pool_count} pools, +{total_added} added, ~{total_updated} updated, "
            f"-{total_flagged} flagged, {total_errors} errors"
        )

    background_tasks.add_task(run_all_pool_syncs)

    return {
        "status": "accepted",
        "orchestrator_id": orchestrator.id,
        "orchestrator_name": orchestrator.name,
        "pools_queued": pool_count,
        "parallel": parallel,
        "pools": pool_info,
        "note": f"{'Parallel' if parallel else 'Sequential'} sync started for all source pools"
    }


# ========== Webhook Pause Management Endpoints ==========

@router.post("/pause/{orchestrator_id}")
async def pause_orchestrator_webhooks(
    orchestrator_id: int,
    reason: str = "manual",
    ttl_seconds: int = 3600,
    db: Session = Depends(get_db)
):
    """
    Temporarily pause webhook processing for an orchestrator.

    Use this before running bulk import operations that would flood webhooks.
    Paused orchestrators still acknowledge webhooks (200 OK) but skip all processing.

    Args:
        orchestrator_id: ID of the orchestrator to pause
        reason: Reason for pausing (for logging)
        ttl_seconds: How long to pause (default: 1 hour, max: 24 hours)

    Example:
        POST /api/orchestrator/webhook/pause/2?reason=bulk_import&ttl_seconds=1800
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Limit TTL to 24 hours max
    ttl_seconds = min(ttl_seconds, 86400)

    if pause_webhooks_for_orchestrator(orchestrator_id, reason, ttl_seconds):
        return {
            "status": "paused",
            "orchestrator_id": orchestrator.id,
            "orchestrator_name": orchestrator.name,
            "reason": reason,
            "ttl_seconds": ttl_seconds,
            "note": f"Webhooks paused for {ttl_seconds}s. Call /resume/{orchestrator_id} to resume early."
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to pause webhooks (Redis error)")


@router.post("/resume/{orchestrator_id}")
async def resume_orchestrator_webhooks(
    orchestrator_id: int,
    db: Session = Depends(get_db)
):
    """
    Resume webhook processing for a paused orchestrator.

    Args:
        orchestrator_id: ID of the orchestrator to resume

    Example:
        POST /api/orchestrator/webhook/resume/2
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    if resume_webhooks_for_orchestrator(orchestrator_id):
        return {
            "status": "resumed",
            "orchestrator_id": orchestrator.id,
            "orchestrator_name": orchestrator.name,
            "note": "Webhook processing resumed"
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to resume webhooks (Redis error)")


@router.get("/pause-status/{orchestrator_id}")
async def get_orchestrator_pause_status(
    orchestrator_id: int,
    db: Session = Depends(get_db)
):
    """
    Check if webhook processing is paused for an orchestrator.

    Args:
        orchestrator_id: ID of the orchestrator to check

    Example:
        GET /api/orchestrator/webhook/pause-status/2
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    paused, reason = is_webhook_paused(orchestrator_id)

    return {
        "orchestrator_id": orchestrator.id,
        "orchestrator_name": orchestrator.name,
        "enabled": orchestrator.enabled,
        "webhook_paused": paused,
        "pause_reason": reason,
        "status": "paused" if paused else ("disabled" if not orchestrator.enabled else "active")
    }
