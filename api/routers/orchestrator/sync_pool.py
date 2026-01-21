"""
DPSK Orchestrator Pool Sync Module.

This module contains the unified sync logic for synchronizing passphrases
from a single source pool to the site-wide pool. It's the single source of truth
for passphrase sync operations, used by both:
- Webhook handlers (single pool triggered by R1 activity)
- Full sync engine (multiple pools in parallel)

Key design: One pool, one sync. Higher-level orchestration (parallel execution,
full diff, scheduling) is handled by callers.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from models.orchestrator import (
    DPSKOrchestrator,
    OrchestratorSourcePool,
    OrchestratorSyncEvent,
    PassphraseMapping
)
from r1api.client import R1Client

logger = logging.getLogger(__name__)


@dataclass
class PoolSyncResult:
    """Result of syncing a single pool."""
    pool_id: str
    pool_name: str
    added: int = 0
    updated: int = 0
    flagged: int = 0  # Passphrases deleted from source, flagged for review
    skipped: int = 0  # Already synced, no changes needed
    errors: List[str] = field(default_factory=list)
    source_count: int = 0  # Total passphrases in source pool


def normalize_vlan(vlan_id) -> Optional[int]:
    """
    Normalize VLAN ID to consistent type for comparison.

    Handles: int, str, None, empty string, '0', 0
    Returns: int or None
    """
    if vlan_id is None or vlan_id == '' or vlan_id == 0 or vlan_id == '0':
        return None
    try:
        return int(vlan_id)
    except (ValueError, TypeError):
        return None


def get_username(pp: Dict) -> str:
    """Get username from passphrase dict, handling API field name inconsistency."""
    return pp.get('username') or pp.get('userName') or ''


async def sync_single_pool(
    db: Session,
    r1_client: R1Client,
    orchestrator: DPSKOrchestrator,
    source_pool: OrchestratorSourcePool,
    specific_passphrase_ids: Optional[List[str]] = None,
    sync_event: Optional[OrchestratorSyncEvent] = None
) -> PoolSyncResult:
    """
    Sync a single source pool to the site-wide pool.

    This is the core sync function used by both webhooks and full sync.
    It handles:
    - Fetching passphrases (all or specific IDs)
    - Adding new passphrases to site-wide
    - Detecting deleted passphrases and flagging them
    - Creating/updating mapping records

    Args:
        db: Database session
        r1_client: Initialized R1 API client
        orchestrator: The DPSK orchestrator config
        source_pool: The source pool to sync from
        specific_passphrase_ids: Optional list of specific passphrase IDs to sync.
                                 If None, scans entire pool for new passphrases.
        sync_event: Optional sync event for tracking (created by caller)

    Returns:
        PoolSyncResult with counts and any errors
    """
    result = PoolSyncResult(
        pool_id=source_pool.pool_id,
        pool_name=source_pool.pool_name or "Unknown"
    )

    try:
        logger.info(f"Starting sync for pool '{source_pool.pool_name}' -> site-wide")

        # 1. Fetch passphrases from source pool
        if specific_passphrase_ids:
            # Fetch specific passphrases by ID (webhook scenario with known IDs)
            source_passphrases = []
            for pp_id in specific_passphrase_ids:
                try:
                    pp = await r1_client.dpsk.get_passphrase(
                        pool_id=source_pool.pool_id,
                        passphrase_id=pp_id,
                        tenant_id=orchestrator.tenant_id
                    )
                    if pp:
                        source_passphrases.append(pp)
                except Exception as e:
                    logger.warning(f"Failed to fetch passphrase {pp_id}: {e}")
                    result.errors.append(f"Failed to fetch passphrase {pp_id}: {e}")
        else:
            # Fetch all passphrases from source pool (full scan)
            source_passphrases = await _fetch_pool_passphrases(
                r1_client, source_pool.pool_id, orchestrator.tenant_id
            )

        result.source_count = len(source_passphrases)
        logger.info(f"Fetched {result.source_count} passphrases from {source_pool.pool_name}")

        # 2. Get existing mappings for this pool
        existing_mappings = db.query(PassphraseMapping).filter(
            PassphraseMapping.orchestrator_id == orchestrator.id,
            PassphraseMapping.source_pool_id == source_pool.pool_id
        ).all()
        mapping_by_source_id = {m.source_passphrase_id: m for m in existing_mappings if m.source_passphrase_id}

        # Track which source passphrases we've seen (for deletion detection)
        seen_source_ids: Set[str] = set()

        # 3. Process each source passphrase
        for pp in source_passphrases:
            pp_id = pp.get('id')
            if not pp_id:
                continue

            seen_source_ids.add(pp_id)

            if pp_id in mapping_by_source_id:
                # Already synced - check if needs update
                mapping = mapping_by_source_id[pp_id]
                if mapping.sync_status == "synced":
                    # Check for updates (VLAN change, etc.)
                    if await _check_and_update_if_needed(
                        db, r1_client, orchestrator, mapping, pp, result.errors
                    ):
                        result.updated += 1
                    else:
                        result.skipped += 1
                elif mapping.sync_status == "target_missing":
                    # Target was deleted externally - re-create it
                    if await add_passphrase_to_sitewide(
                        db, r1_client, orchestrator, source_pool, pp, result.errors
                    ):
                        result.added += 1
                        # Remove old mapping with target_missing status
                        db.delete(mapping)
                else:
                    result.skipped += 1  # Other status (flagged, orphan)
            else:
                # New passphrase - add to site-wide
                if await add_passphrase_to_sitewide(
                    db, r1_client, orchestrator, source_pool, pp, result.errors
                ):
                    result.added += 1

        # 4. Detect deletions (only if we did a full scan, not specific IDs)
        if not specific_passphrase_ids:
            for mapping in existing_mappings:
                if (mapping.source_passphrase_id and
                    mapping.source_passphrase_id not in seen_source_ids and
                    mapping.sync_status == "synced"):
                    # Source passphrase was deleted - flag for review
                    mapping.sync_status = "flagged_removal"
                    mapping.flagged_at = datetime.utcnow()
                    result.flagged += 1
                    logger.info(f"Flagged deleted passphrase: {mapping.source_username}")

        # 5. Update source pool tracking
        source_pool.last_sync_at = datetime.utcnow()
        source_pool.passphrase_count = result.source_count

        # 6. Update sync event if provided
        if sync_event:
            sync_event.status = "success" if not result.errors else "partial"
            sync_event.added_count = result.added
            sync_event.updated_count = result.updated
            sync_event.flagged_for_removal = result.flagged
            sync_event.errors = result.errors
            sync_event.completed_at = datetime.utcnow()

        db.commit()

        logger.info(
            f"Pool sync complete: {source_pool.pool_name} -> "
            f"+{result.added} added, ~{result.updated} updated, "
            f"-{result.flagged} flagged, ={result.skipped} unchanged"
        )

    except Exception as e:
        error_msg = f"Pool sync failed for {source_pool.pool_name}: {e}"
        logger.error(error_msg)
        result.errors.append(error_msg)

        if sync_event:
            sync_event.status = "failed"
            sync_event.errors = result.errors
            sync_event.completed_at = datetime.utcnow()
            db.commit()

    return result


async def add_passphrase_to_sitewide(
    db: Session,
    r1_client: R1Client,
    orchestrator: DPSKOrchestrator,
    source_pool: OrchestratorSourcePool,
    source_pp: Dict[str, Any],
    errors: List[str]
) -> bool:
    """
    Add a single passphrase from source pool to site-wide pool.

    This function:
    1. Creates the passphrase in the site-wide DPSK pool
    2. Updates the auto-generated identity with proper name/description
    3. Creates a mapping record for tracking

    The username in site-wide is made unique by appending the source pool name:
    e.g., "JohnDoe [Unit104]" for traceability and uniqueness.

    Args:
        db: Database session
        r1_client: Initialized R1 API client
        orchestrator: The orchestrator config
        source_pool: The source pool this passphrase came from
        source_pp: The source passphrase dict from R1 API
        errors: List to append any errors to

    Returns:
        True if added successfully, False otherwise
    """
    source_pp_id = source_pp.get('id')
    passphrase_str = source_pp.get('passphrase', '')
    original_username = get_username(source_pp)
    vlan_id = normalize_vlan(source_pp.get('vlanId'))
    pool_name = source_pool.pool_name or source_pool.pool_id[:8]

    # Check if we already have a mapping for this source passphrase
    existing_mapping = db.query(PassphraseMapping).filter_by(
        orchestrator_id=orchestrator.id,
        source_passphrase_id=source_pp_id
    ).first()

    if existing_mapping and existing_mapping.sync_status == "synced":
        logger.debug(f"Passphrase {original_username} already mapped, skipping")
        return False

    # Check if same passphrase+VLAN already exists in site-wide (manual entry case)
    existing_in_sitewide = await _find_passphrase_in_pool(
        r1_client, orchestrator.site_wide_pool_id, orchestrator.tenant_id,
        passphrase_str, vlan_id
    )

    if existing_in_sitewide:
        logger.info(f"Passphrase {original_username} already exists in site-wide, creating mapping only")
        mapping = PassphraseMapping(
            orchestrator_id=orchestrator.id,
            source_pool_id=source_pool.pool_id,
            source_pool_name=pool_name,
            source_passphrase_id=source_pp_id,
            source_username=original_username,
            source_identity_id=source_pp.get('identityId'),
            target_passphrase_id=existing_in_sitewide.get('id'),
            target_identity_id=existing_in_sitewide.get('identityId'),
            sync_status="synced",
            vlan_id=vlan_id,
            passphrase_preview=passphrase_str[:4] + "****" if passphrase_str else None,
            last_synced_at=datetime.utcnow()
        )
        db.add(mapping)
        db.commit()
        return True

    # Build unique username: "OriginalUsername [SourcePool]"
    if original_username:
        unique_username = f"{original_username} [{pool_name}]"
    else:
        unique_username = f"[{pool_name}]"

    try:
        # 1. Create passphrase in site-wide pool
        result = await r1_client.dpsk.create_passphrase(
            pool_id=orchestrator.site_wide_pool_id,
            tenant_id=orchestrator.tenant_id,
            user_name=unique_username,
            user_email=source_pp.get('userEmail') or source_pp.get('email'),
            passphrase=passphrase_str,
            vlan_id=source_pp.get('vlanId'),  # Pass original value for API
            max_devices=source_pp.get('maxDevices') or source_pp.get('numberOfDevices', 5),
            expiration_date=source_pp.get('expirationDate')
        )

        if not result or not result.get('id'):
            error_msg = f"No ID returned for passphrase {unique_username}"
            logger.error(error_msg)
            errors.append(error_msg)
            return False

        target_pp_id = result.get('id')
        target_identity_id = result.get('identityId')

        # 2. Update the auto-generated identity with proper details
        if target_identity_id and orchestrator.site_wide_identity_group_id:
            try:
                # Build descriptive identity info
                if original_username:
                    identity_display_name = f"{original_username} (from {pool_name})"
                else:
                    identity_display_name = f"(from {pool_name})"
                identity_description = f"Synced from {pool_name}"

                await r1_client.identity.update_identity(
                    group_id=orchestrator.site_wide_identity_group_id,
                    identity_id=target_identity_id,
                    tenant_id=orchestrator.tenant_id,
                    name=unique_username,
                    display_name=identity_display_name,
                    description=identity_description,
                    vlan=vlan_id
                )
                logger.debug(f"Updated identity {target_identity_id}: name={unique_username}")
            except Exception as e:
                # Non-fatal: passphrase was created, identity update failed
                logger.warning(f"Failed to update identity {target_identity_id}: {e}")

        # 3. Create mapping record
        mapping = PassphraseMapping(
            orchestrator_id=orchestrator.id,
            source_pool_id=source_pool.pool_id,
            source_pool_name=pool_name,
            source_passphrase_id=source_pp_id,
            source_username=original_username,  # Store original, not unique
            source_identity_id=source_pp.get('identityId'),
            target_passphrase_id=target_pp_id,
            target_identity_id=target_identity_id,
            sync_status="synced",
            vlan_id=vlan_id,
            passphrase_preview=passphrase_str[:4] + "****" if passphrase_str else None,
            last_synced_at=datetime.utcnow()
        )
        db.add(mapping)
        db.commit()

        logger.info(f"Added passphrase to site-wide: {unique_username} (VLAN: {vlan_id})")
        return True

    except Exception as e:
        error_msg = f"Failed to add {original_username} to site-wide: {e}"
        logger.error(error_msg)
        errors.append(error_msg)
        return False


async def _fetch_pool_passphrases(
    r1_client: R1Client,
    pool_id: str,
    tenant_id: str
) -> List[Dict[str, Any]]:
    """Fetch all passphrases from a pool with pagination."""
    all_passphrases = []
    page = 1

    while True:
        try:
            result = await r1_client.dpsk.query_passphrases(
                pool_id=pool_id,
                tenant_id=tenant_id,
                page=page,
                limit=500
            )
            passphrases = result.get('data', [])
            if not passphrases:
                break

            all_passphrases.extend(passphrases)

            total_count = result.get('totalCount', len(passphrases))
            if len(all_passphrases) >= total_count:
                break

            page += 1

        except Exception as e:
            logger.error(f"Failed to fetch passphrases from pool {pool_id}: {e}")
            break

    return all_passphrases


async def _find_passphrase_in_pool(
    r1_client: R1Client,
    pool_id: str,
    tenant_id: str,
    passphrase_str: str,
    vlan_id: Optional[int]
) -> Optional[Dict[str, Any]]:
    """
    Find a passphrase in a pool by its passphrase string and VLAN.

    Returns:
        Passphrase dict if found, None otherwise
    """
    if not passphrase_str:
        return None

    try:
        result = await r1_client.dpsk.query_passphrases(
            pool_id=pool_id,
            tenant_id=tenant_id,
            page=1,
            limit=500
        )

        for pp in result.get('data', []):
            if pp.get('passphrase') == passphrase_str:
                pp_vlan = normalize_vlan(pp.get('vlanId'))
                if pp_vlan == vlan_id:
                    return pp
    except Exception as e:
        logger.debug(f"Error searching for passphrase in pool: {e}")

    return None


async def _check_and_update_if_needed(
    db: Session,
    r1_client: R1Client,
    orchestrator: DPSKOrchestrator,
    mapping: PassphraseMapping,
    source_pp: Dict[str, Any],
    errors: List[str]
) -> bool:
    """
    Check if a synced passphrase needs updating and update if necessary.

    Compares VLAN and other relevant fields. If different, updates target.

    Returns:
        True if updated, False if no update needed
    """
    source_vlan = normalize_vlan(source_pp.get('vlanId'))

    # Quick check: VLAN changed?
    if source_vlan != mapping.vlan_id:
        try:
            await r1_client.dpsk.update_passphrase(
                pool_id=orchestrator.site_wide_pool_id,
                passphrase_id=mapping.target_passphrase_id,
                tenant_id=orchestrator.tenant_id,
                vlan_id=source_pp.get('vlanId')
            )

            # Also update identity VLAN if we have identity info
            if mapping.target_identity_id and orchestrator.site_wide_identity_group_id:
                try:
                    await r1_client.identity.update_identity(
                        group_id=orchestrator.site_wide_identity_group_id,
                        identity_id=mapping.target_identity_id,
                        tenant_id=orchestrator.tenant_id,
                        vlan=source_vlan
                    )
                except Exception as e:
                    logger.warning(f"Failed to update identity VLAN: {e}")

            mapping.vlan_id = source_vlan
            mapping.last_synced_at = datetime.utcnow()
            db.commit()

            logger.info(f"Updated VLAN for {mapping.source_username}: {mapping.vlan_id} -> {source_vlan}")
            return True

        except Exception as e:
            error_msg = f"Failed to update passphrase {mapping.source_username}: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    return False


# ========== Convenience Function for Webhook/API Callers ==========

async def sync_pool_by_id(
    db: Session,
    r1_client: R1Client,
    orchestrator: DPSKOrchestrator,
    pool_id: str,
    specific_passphrase_ids: Optional[List[str]] = None,
    create_sync_event: bool = True,
    event_type: str = "webhook"
) -> PoolSyncResult:
    """
    Sync a pool by ID, handling source pool lookup and sync event creation.

    This is a convenience wrapper around sync_single_pool() that:
    - Looks up the source pool by ID (with UUID normalization)
    - Optionally creates a sync event for tracking
    - Returns the sync result

    Args:
        db: Database session
        r1_client: Initialized R1 API client
        orchestrator: The orchestrator config
        pool_id: Source pool ID (handles UUID format differences)
        specific_passphrase_ids: Optional list of specific passphrase IDs
        create_sync_event: Whether to create a sync event record
        event_type: Type of sync event ("webhook", "manual", "scheduled")

    Returns:
        PoolSyncResult

    Raises:
        ValueError: If pool_id is not a source pool for this orchestrator
    """
    # Find source pool (handles UUID normalization)
    source_pool = _find_source_pool(orchestrator, pool_id)

    if not source_pool:
        raise ValueError(
            f"Pool {pool_id} is not a source pool for orchestrator {orchestrator.id}. "
            f"Configured pools: {[p.pool_id for p in orchestrator.source_pools]}"
        )

    # Create sync event if requested
    sync_event = None
    if create_sync_event:
        sync_event = OrchestratorSyncEvent(
            orchestrator_id=orchestrator.id,
            event_type=event_type,
            source_pool_id=source_pool.pool_id,
            status="running",
            started_at=datetime.utcnow()
        )
        db.add(sync_event)
        db.commit()

    return await sync_single_pool(
        db=db,
        r1_client=r1_client,
        orchestrator=orchestrator,
        source_pool=source_pool,
        specific_passphrase_ids=specific_passphrase_ids,
        sync_event=sync_event
    )


def _find_source_pool(
    orchestrator: DPSKOrchestrator,
    pool_id: str
) -> Optional[OrchestratorSourcePool]:
    """
    Find a source pool by ID, handling UUID format differences.

    Checks both pool_id and identity_group_id with/without hyphens.
    """
    if not pool_id:
        return None

    normalized = pool_id.replace("-", "").lower()

    for pool in orchestrator.source_pools:
        # Check pool_id
        if pool.pool_id.replace("-", "").lower() == normalized:
            return pool
        # Check identity_group_id
        if pool.identity_group_id and pool.identity_group_id.replace("-", "").lower() == normalized:
            return pool

    return None
