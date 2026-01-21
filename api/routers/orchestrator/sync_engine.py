"""
DPSK Orchestrator Sync Engine.

This module contains the core sync logic for synchronizing passphrases
from per-unit DPSK pools to a site-wide DPSK pool.
"""
import asyncio
import fnmatch
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database import SessionLocal
from models.controller import Controller
from models.orchestrator import (
    DPSKOrchestrator,
    OrchestratorSourcePool,
    OrchestratorSyncEvent,
    PassphraseMapping
)
from clients.r1_client import create_r1_client_from_controller
from r1api.client import R1Client
from routers.orchestrator.sync_pool import (
    sync_single_pool,
    add_passphrase_to_sitewide,
    normalize_vlan,
    get_username,
    PoolSyncResult
)

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """
    Result of a sync operation.

    Terminology:
    - added: New passphrases synced from source → site-wide
    - updated: Existing passphrases updated in site-wide
    - flagged: Passphrases we previously synced, but source was deleted
               (awaiting manual decision: delete from site-wide or keep?)
    - orphans: Passphrases in site-wide that we never synced
               (existed before orchestrator, or manually created)
    - duplicates: Same passphrase+VLAN found in multiple source pools
                  (conflict - only one can be synced)
    - stale_cleaned: Mappings where target no longer exists (marked target_missing)
    """
    added: int = 0
    updated: int = 0
    flagged: int = 0
    orphans: int = 0
    duplicates: int = 0
    skipped: int = 0  # Already synced, no changes needed
    stale_cleaned: int = 0  # Mappings where target passphrase no longer exists
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    pools_scanned: int = 0
    pools_discovered: int = 0

    # Summary counts for logging
    source_pool_count: int = 0
    source_passphrase_count: int = 0
    site_wide_initial_count: int = 0


class SyncEngine:
    """
    Core sync engine for DPSK Orchestrator.

    Handles synchronization of passphrases from per-unit pools to the site-wide pool.
    """

    def __init__(self, orchestrator_id: int, db: Optional[Session] = None):
        self.orchestrator_id = orchestrator_id
        self.db = db or SessionLocal()
        self._owns_db = db is None  # Track if we created the session
        self.orchestrator: Optional[DPSKOrchestrator] = None
        self.r1_client: Optional[R1Client] = None
        self._rate_limiter = asyncio.Semaphore(120)  # Max concurrent requests

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owns_db:
            self.db.close()

    async def initialize(self):
        """Load orchestrator config and initialize API client."""
        self.orchestrator = self.db.query(DPSKOrchestrator).get(self.orchestrator_id)
        if not self.orchestrator:
            raise ValueError(f"Orchestrator {self.orchestrator_id} not found")

        # Initialize R1 client using the helper function
        # This handles credential decryption and authentication automatically
        self.r1_client = create_r1_client_from_controller(
            self.orchestrator.controller_id,
            self.db
        )

    async def _rate_limited(self, coro):
        """Execute a coroutine with rate limiting."""
        async with self._rate_limiter:
            return await coro

    # ========== Full Sync ==========

    async def full_sync(self, event_type: str = "scheduled") -> SyncResult:
        """
        Perform a full diff sync between all source pools and the site-wide pool.

        Args:
            event_type: Type of sync event ("scheduled", "manual", "webhook")

        This is used by the scheduler as a backup to webhook-triggered syncs.
        """
        result = SyncResult()
        sync_event = self._create_sync_event(event_type)

        try:
            # ========== PHASE 1: Gather Data ==========
            logger.info(f"=== Starting sync for orchestrator '{self.orchestrator.name}' ===")

            # 1. Validate pool compatibility (passphrase length, etc.)
            compatibility_warnings = await self._validate_pool_compatibility()
            if compatibility_warnings:
                result.warnings.extend(compatibility_warnings)

            # 2. Fetch all passphrases from site-wide pool FIRST (baseline)
            site_wide_passphrases = await self._fetch_site_wide_passphrases()
            result.site_wide_initial_count = len(site_wide_passphrases)

            # 3. Fetch all passphrases from all source pools
            source_passphrases = await self._fetch_all_source_passphrases()
            result.source_pool_count = len(self.orchestrator.source_pools)
            result.source_passphrase_count = len(source_passphrases)

            # ========== PHASE 2: Verify Existing Mappings ==========
            # Check that existing mappings still have valid targets in site-wide pool
            # This catches cases where passphrases were deleted externally
            site_wide_ids = {pp.get('id') for pp in site_wide_passphrases if pp.get('id')}

            existing_mappings = self.db.query(PassphraseMapping).filter(
                PassphraseMapping.orchestrator_id == self.orchestrator_id,
                PassphraseMapping.target_passphrase_id.isnot(None),
                PassphraseMapping.sync_status.in_(["synced", "flagged_removal"])
            ).all()

            stale_mappings = []
            for mapping in existing_mappings:
                if mapping.target_passphrase_id not in site_wide_ids:
                    stale_mappings.append(mapping)

            if stale_mappings:
                logger.info(f"--- Cleaning up {len(stale_mappings)} stale mapping(s) ---")
                for mapping in stale_mappings:
                    old_status = mapping.sync_status
                    mapping.sync_status = "target_missing"
                    mapping.flagged_at = datetime.utcnow()
                    result.stale_cleaned += 1
                    logger.warning(
                        f"Marked mapping as target_missing: {mapping.source_username} "
                        f"(was {old_status}, target {mapping.target_passphrase_id} no longer exists)"
                    )
                self.db.commit()

            # ========== PHASE 3: Summary Logging ==========
            # Build per-pool breakdown
            pool_counts = {}
            for pp in source_passphrases:
                pool_name = pp.get('_source_pool_name', 'Unknown')
                pool_counts[pool_name] = pool_counts.get(pool_name, 0) + 1

            logger.info(f"--- Initial State ---")
            logger.info(f"  Site-wide pool: {result.site_wide_initial_count} passphrases")
            logger.info(f"  Source pools: {result.source_pool_count} pools, {result.source_passphrase_count} total passphrases")
            for pool_name, count in sorted(pool_counts.items()):
                logger.info(f"    - {pool_name}: {count} passphrases")
            if result.stale_cleaned > 0:
                logger.info(f"  Stale mappings cleaned: {result.stale_cleaned}")

            # ========== PHASE 4: Build Maps & Detect Duplicates ==========
            # Key: (passphrase, vlan_id) - the actual passphrase + VLAN is the unique identifier
            # Note: userName is NOT unique, but the passphrase string itself is

            # Detect duplicates: same passphrase+VLAN in multiple source pools
            source_map = {}
            duplicate_keys = set()
            for pp in source_passphrases:
                passphrase_str = pp.get('passphrase', '')
                if not passphrase_str:
                    continue

                key = (passphrase_str, self._normalize_vlan(pp.get('vlanId')))
                if key in source_map:
                    # Duplicate found!
                    existing_pp = source_map[key]
                    existing_pool = existing_pp.get('_source_pool_name', 'Unknown')
                    new_pool = pp.get('_source_pool_name', 'Unknown')

                    if key not in duplicate_keys:
                        # First time seeing this duplicate
                        duplicate_keys.add(key)
                        result.duplicates += 1
                        warning = (
                            f"Duplicate passphrase found: '{passphrase_str[:8]}...' (VLAN {key[1]}) "
                            f"exists in both '{existing_pool}' and '{new_pool}'. "
                            f"Only the first occurrence will be synced."
                        )
                        result.warnings.append(warning)
                        logger.warning(warning)
                else:
                    source_map[key] = pp

            site_wide_map = {
                (p.get('passphrase', ''), self._normalize_vlan(p.get('vlanId'))): p
                for p in site_wide_passphrases
                if p.get('passphrase')
            }

            # Log duplicate summary if any
            if result.duplicates > 0:
                logger.warning(f"--- Duplicates: {result.duplicates} passphrase(s) exist in multiple source pools ---")

            # ========== PHASE 5: Calculate Diff ==========
            to_add = []
            to_update = []
            to_flag = []
            to_orphan = []
            already_synced = 0

            # Find passphrases to ADD (in source but not in site-wide)
            for key, source_pp in source_map.items():
                if key not in site_wide_map:
                    to_add.append(source_pp)
                else:
                    # Already in site-wide - check if needs update
                    site_pp = site_wide_map[key]
                    if self._needs_update(source_pp, site_pp):
                        to_update.append((source_pp, site_pp))
                    else:
                        already_synced += 1

            # Find passphrases to FLAG or mark as ORPHAN
            for key, site_pp in site_wide_map.items():
                if key not in source_map:
                    mapping = self._get_mapping_by_target(site_pp.get('id'))
                    if mapping:
                        to_flag.append((site_pp, mapping))
                    else:
                        to_orphan.append(site_pp)

            result.skipped = already_synced

            logger.info(f"--- Sync Plan ---")
            logger.info(f"  To add: {len(to_add)} (new passphrases to sync)")
            logger.info(f"  To update: {len(to_update)} (existing passphrases with changes)")
            logger.info(f"  Already synced: {already_synced} (no changes needed)")
            logger.info(f"  To flag: {len(to_flag)} (source deleted, needs review)")
            logger.info(f"  Orphans: {len(to_orphan)} (in site-wide but not from any source)")
            if result.warnings:
                logger.info(f"  Warnings: {len(result.warnings)}")

            # ========== PHASE 6: Execute Sync ==========
            logger.info(f"--- Executing Sync ---")

            # ADD new passphrases
            for source_pp in to_add:
                if await self._sync_passphrase_add(source_pp, result.errors):
                    result.added += 1

            # UPDATE existing passphrases
            for source_pp, site_pp in to_update:
                if await self._sync_passphrase_update(source_pp, site_pp):
                    result.updated += 1

            # FLAG removed passphrases
            for site_pp, mapping in to_flag:
                await self._flag_passphrase_removal(mapping)
                result.flagged += 1

            # Mark orphans
            for site_pp in to_orphan:
                await self._flag_orphan(site_pp)
                result.orphans += 1

            # ========== PHASE 7: Finalize ==========
            sync_event.status = "success" if not result.errors else "partial"
            sync_event.added_count = result.added
            sync_event.updated_count = result.updated
            sync_event.flagged_for_removal = result.flagged
            sync_event.orphans_found = result.orphans
            sync_event.errors = result.errors + result.warnings  # Include warnings in event
            sync_event.completed_at = datetime.utcnow()

            # Update last_sync_at on orchestrator
            new_sync_time = datetime.utcnow()
            old_sync_time = self.orchestrator.last_sync_at
            self.orchestrator.last_sync_at = new_sync_time
            logger.debug(f"Updating last_sync_at: {old_sync_time} -> {new_sync_time} (event_id={sync_event.id})")

            self.db.commit()
            logger.debug(f"Committed sync event {sync_event.id} and last_sync_at={new_sync_time}")

            # Final summary
            logger.info(f"=== Sync Complete for '{self.orchestrator.name}' ===")
            logger.info(f"  Added: {result.added}, Updated: {result.updated}, Skipped: {result.skipped}")
            logger.info(f"  Flagged: {result.flagged}, Orphans: {result.orphans}, Duplicates: {result.duplicates}")
            if result.stale_cleaned > 0:
                logger.info(f"  Stale mappings cleaned: {result.stale_cleaned} (target no longer exists)")
            if result.errors:
                logger.warning(f"  Errors: {len(result.errors)}")
            logger.info(f"  Site-wide pool: {result.site_wide_initial_count} → {result.site_wide_initial_count + result.added} passphrases")

        except Exception as e:
            sync_event.status = "failed"
            sync_event.errors = [str(e)]
            sync_event.completed_at = datetime.utcnow()
            self.db.commit()
            logger.error(f"Full sync failed: {e}")
            result.errors.append(str(e))

        return result

    # ========== Pool Sync (Incremental) ==========

    async def sync_pool(self, pool_id: str) -> SyncResult:
        """
        Sync a single source pool (triggered by webhook).

        More efficient than full_sync for handling individual pool changes.
        Delegates to the unified sync_single_pool function.
        """
        result = SyncResult()

        try:
            # Find source pool
            source_pool = self.db.query(OrchestratorSourcePool).filter_by(
                orchestrator_id=self.orchestrator_id,
                pool_id=pool_id
            ).first()

            if not source_pool:
                logger.warning(f"Pool {pool_id} is not a source pool for orchestrator {self.orchestrator_id}")
                result.errors.append(f"Pool {pool_id} not found in source pools")
                return result

            # Create sync event
            sync_event = self._create_sync_event("webhook", source_pool_id=pool_id)

            # Use unified sync function
            pool_result = await sync_single_pool(
                db=self.db,
                r1_client=self.r1_client,
                orchestrator=self.orchestrator,
                source_pool=source_pool,
                sync_event=sync_event
            )

            # Map PoolSyncResult to SyncResult
            result.added = pool_result.added
            result.updated = pool_result.updated
            result.flagged = pool_result.flagged
            result.skipped = pool_result.skipped
            result.errors = pool_result.errors

            logger.info(f"Pool sync complete for {pool_id}: +{result.added}, ~{result.updated}, -{result.flagged}")

        except Exception as e:
            logger.error(f"Pool sync failed for {pool_id}: {e}")
            result.errors.append(str(e))

        return result

    # ========== Auto-Discovery ==========

    async def auto_discover_source_pools(self) -> SyncResult:
        """
        Discover new per-unit pools that should be added as sources.

        Uses venue scoping and include/exclude patterns.
        """
        result = SyncResult()

        try:
            # 1. Get all DPSK pools (filtered by venue if specified)
            all_pools = await self._rate_limited(
                self.r1_client.dpsk.query_dpsk_pools(
                    tenant_id=self.orchestrator.tenant_id,
                    page=1,
                    limit=1000
                )
            )
            pools_data = all_pools.get('data', [])
            result.pools_scanned = len(pools_data)

            # 2. Filter by discovery rules
            include_patterns = self.orchestrator.include_patterns or ["*"]
            exclude_patterns = self.orchestrator.exclude_patterns or []

            discovered = []
            for pool in pools_data:
                pool_id = pool.get('id')
                pool_name = pool.get('name', '')

                # Skip the site-wide pool itself
                if pool_id == self.orchestrator.site_wide_pool_id:
                    continue

                # Apply include/exclude patterns
                if self._matches_patterns(pool_name, include_patterns):
                    if not self._matches_patterns(pool_name, exclude_patterns):
                        discovered.append(pool)

            # 3. Find new pools (not already tracked)
            existing_pool_ids = {sp.pool_id for sp in self.orchestrator.source_pools}
            new_pools = [p for p in discovered if p.get('id') not in existing_pool_ids]

            # 4. Add new pools as sources
            for pool in new_pools:
                source_pool = OrchestratorSourcePool(
                    orchestrator_id=self.orchestrator_id,
                    pool_id=pool.get('id'),
                    pool_name=pool.get('name'),
                    identity_group_id=pool.get('identityGroupId'),
                    discovered_at=datetime.utcnow()
                )
                self.db.add(source_pool)
                logger.info(f"Auto-discovered new source pool: {pool.get('name')}")

            result.pools_discovered = len(new_pools)

            # Update orchestrator discovery tracking
            self.orchestrator.last_discovery_at = datetime.utcnow()
            self.orchestrator.discovered_pools_count = len(self.orchestrator.source_pools) + len(new_pools)

            self.db.commit()

        except Exception as e:
            logger.error(f"Auto-discovery failed: {e}")
            result.errors.append(str(e))

        return result

    # ========== Helper Methods ==========

    def _create_sync_event(self, event_type: str, source_pool_id: str = None) -> OrchestratorSyncEvent:
        """Create a sync event record."""
        event = OrchestratorSyncEvent(
            orchestrator_id=self.orchestrator_id,
            event_type=event_type,
            source_pool_id=source_pool_id,
            status="running"
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        # Refresh orchestrator to keep it attached to session after commit
        self.db.refresh(self.orchestrator)
        return event

    async def _fetch_all_source_passphrases(self) -> List[Dict[str, Any]]:
        """Fetch passphrases from all source pools."""
        all_passphrases = []

        for source_pool in self.orchestrator.source_pools:
            passphrases = await self._fetch_pool_passphrases(source_pool.pool_id)
            # Tag each passphrase with its source pool
            for pp in passphrases:
                pp['_source_pool_id'] = source_pool.pool_id
                pp['_source_pool_name'] = source_pool.pool_name
            all_passphrases.extend(passphrases)

        return all_passphrases

    async def _fetch_pool_passphrases(self, pool_id: str) -> List[Dict[str, Any]]:
        """Fetch all passphrases from a single pool."""
        try:
            result = await self._rate_limited(
                self.r1_client.dpsk.query_passphrases(
                    pool_id=pool_id,
                    tenant_id=self.orchestrator.tenant_id,
                    page=1,
                    limit=1000
                )
            )
            return result.get('data', [])
        except Exception as e:
            logger.error(f"Failed to fetch passphrases from pool {pool_id}: {e}")
            return []

    async def _fetch_site_wide_passphrases(self) -> List[Dict[str, Any]]:
        """Fetch all passphrases from site-wide pool."""
        return await self._fetch_pool_passphrases(self.orchestrator.site_wide_pool_id)

    def _normalize_vlan(self, vlan_id) -> Optional[int]:
        """Normalize VLAN ID. Delegates to unified function."""
        return normalize_vlan(vlan_id)

    def _needs_update(self, source_pp: Dict, site_pp: Dict) -> bool:
        """Check if a site-wide passphrase needs to be updated from source."""
        # Compare relevant fields (normalize vlanId for comparison)
        if self._normalize_vlan(source_pp.get('vlanId')) != self._normalize_vlan(site_pp.get('vlanId')):
            return True
        # Compare other fields
        fields_to_compare = ['passphrase', 'maxDevices', 'expirationDate']
        for field in fields_to_compare:
            if source_pp.get(field) != site_pp.get(field):
                return True
        return False

    def _get_username(self, pp: Dict) -> str:
        """Get username from passphrase dict. Delegates to unified function."""
        return get_username(pp) or 'unknown'

    async def _sync_passphrase_add(self, source_pp: Dict, errors: List[str] = None) -> bool:
        """
        Add a passphrase to the site-wide pool.

        Delegates to the unified add_passphrase_to_sitewide function.
        The source passphrase dict should have _source_pool_id and _source_pool_name
        embedded (added during fetch).
        """
        source_pool_id = source_pp.get('_source_pool_id', '')
        source_pool_name = source_pp.get('_source_pool_name', '')

        # Find the source pool object
        source_pool = None
        for pool in self.orchestrator.source_pools:
            if pool.pool_id == source_pool_id:
                source_pool = pool
                break

        if not source_pool:
            # Create a minimal source pool object for the sync function
            # This handles cases where we have pool info embedded but no ORM object
            source_pool = OrchestratorSourcePool(
                orchestrator_id=self.orchestrator_id,
                pool_id=source_pool_id,
                pool_name=source_pool_name
            )

        if errors is None:
            errors = []

        return await add_passphrase_to_sitewide(
            db=self.db,
            r1_client=self.r1_client,
            orchestrator=self.orchestrator,
            source_pool=source_pool,
            source_pp=source_pp,
            errors=errors
        )

    async def _sync_passphrase_update(self, source_pp: Dict, site_pp: Dict) -> bool:
        """Update a passphrase in the site-wide pool."""
        try:
            await self._rate_limited(
                self.r1_client.dpsk.update_passphrase(
                    pool_id=self.orchestrator.site_wide_pool_id,
                    passphrase_id=site_pp.get('id'),
                    tenant_id=self.orchestrator.tenant_id,
                    passphrase=source_pp.get('passphrase'),
                    vlan_id=source_pp.get('vlanId'),
                    max_devices=source_pp.get('maxDevices')
                )
            )

            # Update mapping
            mapping = self._get_mapping_by_target(site_pp.get('id'))
            if mapping:
                mapping.last_synced_at = datetime.utcnow()
                mapping.vlan_id = self._normalize_vlan(source_pp.get('vlanId'))
                self.db.commit()

            logger.debug(f"Updated passphrase {self._get_username(source_pp)} in site-wide pool")
            return True

        except Exception as e:
            logger.error(f"Failed to update passphrase {self._get_username(source_pp)}: {e}")
            return False

    async def _check_and_update_passphrase(self, source_pp: Dict, mapping: PassphraseMapping) -> bool:
        """Check if passphrase needs update and update if necessary."""
        try:
            # Fetch current site-wide passphrase
            site_pp = await self._rate_limited(
                self.r1_client.dpsk.get_passphrase(
                    pool_id=self.orchestrator.site_wide_pool_id,
                    passphrase_id=mapping.target_passphrase_id,
                    tenant_id=self.orchestrator.tenant_id
                )
            )

            if self._needs_update(source_pp, site_pp):
                return await self._sync_passphrase_update(source_pp, site_pp)

        except Exception as e:
            logger.error(f"Failed to check/update passphrase: {e}")

        return False

    def _get_mapping_by_target(self, target_passphrase_id: str) -> Optional[PassphraseMapping]:
        """Get a mapping by target passphrase ID."""
        return self.db.query(PassphraseMapping).filter_by(
            orchestrator_id=self.orchestrator_id,
            target_passphrase_id=target_passphrase_id
        ).first()

    async def _flag_passphrase_removal(self, mapping: PassphraseMapping):
        """Flag a passphrase for removal (don't auto-delete)."""
        mapping.sync_status = "flagged_removal"
        mapping.flagged_at = datetime.utcnow()
        self.db.commit()

        logger.warning(
            f"Flagged passphrase {mapping.source_username} for removal "
            f"(source: {mapping.source_passphrase_id}, target: {mapping.target_passphrase_id})"
        )

    async def _flag_orphan(self, site_pp: Dict):
        """Flag an orphan passphrase (exists in site-wide but not synced by us)."""
        # Try to suggest a source pool based on VLAN
        suggested_pool_id = self._suggest_source_pool(site_pp)

        mapping = PassphraseMapping(
            orchestrator_id=self.orchestrator_id,
            source_pool_id="",
            target_passphrase_id=site_pp.get('id'),
            source_username=self._get_username(site_pp),
            sync_status="orphan",
            suggested_source_pool_id=suggested_pool_id,
            vlan_id=self._normalize_vlan(site_pp.get('vlanId')),
            passphrase_preview=site_pp.get('passphrase', '')[:4] + '****' if site_pp.get('passphrase') else None
        )
        self.db.add(mapping)
        self.db.commit()

        logger.warning(f"Found orphan passphrase: {self._get_username(site_pp)} (VLAN: {site_pp.get('vlanId')})")

    def _suggest_source_pool(self, orphan_pp: Dict) -> Optional[str]:
        """Guess which source pool an orphan might belong to based on VLAN."""
        vlan_id = orphan_pp.get('vlanId')
        if not vlan_id:
            return None

        # Look for a source pool where we've synced passphrases with the same VLAN
        mapping = self.db.query(PassphraseMapping).filter_by(
            orchestrator_id=self.orchestrator_id,
            vlan_id=vlan_id,
            sync_status="synced"
        ).first()

        if mapping:
            return mapping.source_pool_id

        return None

    def _matches_patterns(self, name: str, patterns: List[str]) -> bool:
        """Check if a name matches any of the glob patterns."""
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    async def _validate_pool_compatibility(self) -> List[str]:
        """
        Validate that source pools are compatible with the site-wide pool.

        Returns:
            List of warning/error messages for incompatible pools
        """
        warnings = []

        try:
            # Fetch site-wide pool settings
            site_wide_pool = await self._rate_limited(
                self.r1_client.dpsk.get_dpsk_pool(
                    pool_id=self.orchestrator.site_wide_pool_id,
                    tenant_id=self.orchestrator.tenant_id
                )
            )

            site_wide_pp_length = site_wide_pool.get('passphraseLength', 12)
            site_wide_name = site_wide_pool.get('name', 'Site-Wide Pool')

            logger.info(f"Site-wide pool '{site_wide_name}' requires passphrase length >= {site_wide_pp_length}")

            # Check each source pool
            for source_pool in self.orchestrator.source_pools:
                try:
                    pool_details = await self._rate_limited(
                        self.r1_client.dpsk.get_dpsk_pool(
                            pool_id=source_pool.pool_id,
                            tenant_id=self.orchestrator.tenant_id
                        )
                    )

                    source_pp_length = pool_details.get('passphraseLength', 12)
                    pool_name = pool_details.get('name', source_pool.pool_id)

                    # Check passphrase length compatibility
                    if source_pp_length < site_wide_pp_length:
                        warning = (
                            f"Pool '{pool_name}' has passphrase length {source_pp_length}, "
                            f"but site-wide pool requires >= {site_wide_pp_length}. "
                            f"Passphrases from this pool will fail to sync."
                        )
                        warnings.append(warning)
                        logger.warning(warning)

                except Exception as e:
                    warnings.append(f"Failed to validate pool {source_pool.pool_name}: {e}")
                    logger.error(f"Failed to validate pool {source_pool.pool_name}: {e}")

        except Exception as e:
            warnings.append(f"Failed to fetch site-wide pool settings: {e}")
            logger.error(f"Failed to fetch site-wide pool settings: {e}")

        return warnings


# ========== Standalone function for scheduler ==========

async def run_scheduled_sync(orchestrator_id: int) -> Dict[str, Any]:
    """
    Run a scheduled sync for an orchestrator.

    This function is called by the scheduler service.
    """
    logger.info(f"Running scheduled sync for orchestrator {orchestrator_id}")

    async with SyncEngine(orchestrator_id) as engine:
        # Early exit if orchestrator is disabled (defense in depth)
        if not engine.orchestrator.enabled:
            logger.info(f"Orchestrator {orchestrator_id} is disabled, skipping sync")
            return {
                "added": 0,
                "updated": 0,
                "flagged": 0,
                "orphans": 0,
                "duplicates": 0,
                "skipped": 0,
                "errors": [],
                "warnings": [],
                "orchestrator_skipped": True,
                "reason": "orchestrator_disabled"
            }

        result = await engine.full_sync()

    return {
        "added": result.added,
        "updated": result.updated,
        "flagged": result.flagged,
        "orphans": result.orphans,
        "duplicates": result.duplicates,
        "skipped": result.skipped,
        "stale_cleaned": result.stale_cleaned,
        "errors": result.errors,
        "warnings": result.warnings,
        "source_pool_count": result.source_pool_count,
        "source_passphrase_count": result.source_passphrase_count,
        "site_wide_initial_count": result.site_wide_initial_count
    }
