"""
V2 Phase: Inventory Resources for Cleanup

Scans a venue for resources that need to be deleted.
Supports two modes:
1. Job-specific: Uses created_resources from the job that created them
2. Nuclear: Audits entire venue for ALL matching resources

All downstream delete phases consume the inventory output.
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


class ResourceInventory(BaseModel):
    """Inventory of resources to delete."""
    passphrases: List[Dict[str, Any]] = Field(default_factory=list)
    dpsk_pools: List[Dict[str, Any]] = Field(default_factory=list)
    identities: List[Dict[str, Any]] = Field(default_factory=list)
    identity_groups: List[Dict[str, Any]] = Field(default_factory=list)
    wifi_networks: List[Dict[str, Any]] = Field(default_factory=list)
    ap_groups: List[Dict[str, Any]] = Field(default_factory=list)


@register_phase("inventory", "Inventory Resources")
class InventoryPhase(PhaseExecutor):
    """
    Phase 0: Inventory all resources in the venue for cleanup.

    Scans R1 for passphrases, DPSK pools, identity groups,
    WiFi networks, and AP groups. Optionally filters by name pattern.
    """

    class Inputs(BaseModel):
        name_pattern: Optional[str] = None
        nuclear_mode: bool = False
        all_networks: bool = False
        created_resources: Dict[str, List[Dict[str, Any]]] = Field(
            default_factory=dict
        )

    class Outputs(BaseModel):
        inventory: ResourceInventory = Field(
            default_factory=ResourceInventory
        )
        total_resources: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Inventory resources to delete."""
        mode = "NUCLEAR (venue-wide)" if inputs.nuclear_mode else "job-specific"
        await self.emit(f"Inventorying resources ({mode})...")

        if inputs.nuclear_mode:
            inventory = await self._audit_venue(
                inputs.name_pattern, inputs.all_networks
            )
        else:
            inventory = self._from_created_resources(
                inputs.created_resources
            )

        total = (
            len(inventory.passphrases)
            + len(inventory.dpsk_pools)
            + len(inventory.identities)
            + len(inventory.identity_groups)
            + len(inventory.wifi_networks)
            + len(inventory.ap_groups)
        )

        parts = []
        if inventory.passphrases:
            parts.append(f"{len(inventory.passphrases)} passphrases")
        if inventory.dpsk_pools:
            parts.append(f"{len(inventory.dpsk_pools)} DPSK pools")
        if inventory.identities:
            parts.append(f"{len(inventory.identities)} identities")
        if inventory.identity_groups:
            parts.append(f"{len(inventory.identity_groups)} identity groups")
        if inventory.wifi_networks:
            parts.append(f"{len(inventory.wifi_networks)} networks")
        if inventory.ap_groups:
            parts.append(f"{len(inventory.ap_groups)} AP groups")

        summary = ", ".join(parts) if parts else "no resources found"
        level = "success" if total > 0 else "warning"
        await self.emit(f"Inventory: {summary} ({total} total)", level)

        return self.Outputs(inventory=inventory, total_resources=total)

    async def _audit_venue(
        self, name_pattern: Optional[str], all_networks: bool = False
    ) -> ResourceInventory:
        """Audit entire tenant for all deletable resources.

        This is a comprehensive inventory for testing/cleanup purposes.
        Fetches ALL matching resources regardless of venue.
        """
        import re

        inventory = ResourceInventory()
        pattern = re.compile(name_pattern) if name_pattern else None

        # Track identity groups we've already fetched identities from
        # to avoid duplicates when we also fetch from pattern-matched groups
        fetched_identity_group_ids: set = set()

        # Fetch DPSK pools
        try:
            pools_response = await self.r1_client.dpsk.query_dpsk_pools(
                tenant_id=self.tenant_id
            )
            pools = (
                pools_response
                if isinstance(pools_response, list)
                else pools_response.get(
                    'content', pools_response.get('data', [])
                )
            )

            logger.info(
                f"[Inventory] DPSK pools: {len(pools)} total"
                f" (tenant-scoped)"
            )

            for pool in pools:
                pool_name = pool.get('name', '')
                if pattern and not pattern.search(pool_name):
                    continue

                pool_id = pool.get('id')
                identity_group_id = pool.get('identityGroupId')

                inventory.dpsk_pools.append({
                    'id': pool_id,
                    'name': pool_name,
                })

                # Fetch passphrases in this pool (paginated)
                # Use POST query — GET endpoint can silently return 0
                try:
                    all_passphrases = []
                    page = 1
                    page_size = 500
                    while True:
                        pp_response = (
                            await self.r1_client.dpsk.query_passphrases(
                                pool_id=pool_id,
                                tenant_id=self.tenant_id,
                                page=page,
                                limit=page_size,
                            )
                        )
                        passphrases = (
                            pp_response.get(
                                'data',
                                pp_response.get('content', [])
                            )
                            if isinstance(pp_response, dict)
                            else pp_response
                        ) or []
                        all_passphrases.extend(passphrases)

                        # Check if more pages
                        total = pp_response.get('totalCount', 0) if isinstance(pp_response, dict) else 0
                        if len(all_passphrases) >= total or len(passphrases) < page_size:
                            break
                        page += 1

                    logger.info(
                        f"[Inventory]   Pool {pool_name}: "
                        f"{len(all_passphrases)} passphrases"
                    )
                    for pp in all_passphrases:
                        inventory.passphrases.append({
                            'id': pp.get('id'),
                            'pool_id': pool_id,
                            'username': pp.get(
                                'userName',
                                pp.get('username', pp.get('id'))
                            ),
                        })
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch passphrases for pool "
                        f"{pool_name}: {e}", exc_info=True
                    )

                # Fetch identities from linked identity group
                if identity_group_id and identity_group_id not in fetched_identity_group_ids:
                    fetched_identity_group_ids.add(identity_group_id)
                    try:
                        id_response = (
                            await self.r1_client.identity
                            .get_identities_in_group(
                                group_id=identity_group_id,
                                tenant_id=self.tenant_id,
                            )
                        )
                        # GET endpoint returns {content: [...]} (Spring Boot)
                        identities = (
                            id_response.get(
                                'content',
                                id_response.get('data', [])
                            )
                            if isinstance(id_response, dict)
                            else []
                        ) or []
                        logger.info(
                            f"[Inventory]   Pool {pool_name}: "
                            f"{len(identities)} identities in "
                            f"group {identity_group_id}"
                        )
                        for identity in identities:
                            inventory.identities.append({
                                'id': identity.get('id'),
                                'pool_id': pool_id,
                                'group_id': identity_group_id,
                                'username': identity.get(
                                    'userName',
                                    identity.get('name', '')
                                ),
                            })
                    except Exception as e:
                        logger.warning(
                            f"Failed to fetch identities for group "
                            f"{identity_group_id}: {e}", exc_info=True
                        )

        except Exception as e:
            logger.warning(f"Failed to query DPSK pools: {e}", exc_info=True)

        # Fetch identity groups AND their identities
        # This ensures we get ALL identities, not just DPSK-linked ones
        try:
            ig_response = (
                await self.r1_client.identity.query_identity_groups(
                    tenant_id=self.tenant_id
                )
            )
            ig_items = ig_response.get(
                'content', ig_response.get('data', [])
            )
            logger.info(
                f"[Inventory] Identity groups: {len(ig_items)} total"
                f" (tenant-scoped)"
            )

            for ig in ig_items:
                ig_name = ig.get('name', '')
                ig_id = ig.get('id')
                if pattern and not pattern.search(ig_name):
                    continue
                inventory.identity_groups.append({
                    'id': ig_id,
                    'name': ig_name,
                })

                # Fetch ALL identities in this group (not just DPSK-linked)
                # Skip if we already fetched from DPSK pool link above
                if ig_id and ig_id not in fetched_identity_group_ids:
                    fetched_identity_group_ids.add(ig_id)
                    try:
                        id_response = (
                            await self.r1_client.identity
                            .get_identities_in_group(
                                group_id=ig_id,
                                tenant_id=self.tenant_id,
                            )
                        )
                        identities = (
                            id_response.get(
                                'content',
                                id_response.get('data', [])
                            )
                            if isinstance(id_response, dict)
                            else []
                        ) or []
                        if identities:
                            logger.info(
                                f"[Inventory]   Group {ig_name}: "
                                f"{len(identities)} identities"
                            )
                            for identity in identities:
                                inventory.identities.append({
                                    'id': identity.get('id'),
                                    'pool_id': None,  # Not DPSK-linked
                                    'group_id': ig_id,
                                    'username': identity.get(
                                        'userName',
                                        identity.get('name', '')
                                    ),
                                })
                    except Exception as e:
                        logger.warning(
                            f"Failed to fetch identities for group "
                            f"{ig_name}: {e}", exc_info=True
                        )
        except Exception as e:
            logger.warning(f"Failed to query identity groups: {e}", exc_info=True)

        # Fetch WiFi networks (optionally filtered to this venue)
        try:
            nw_response = await self.r1_client.networks.get_wifi_networks(
                tenant_id=self.tenant_id
            )
            fetched_networks = nw_response.get('data', [])
            filter_label = (
                "all (no venue filter)"
                if all_networks
                else f"filtering to venue {self.venue_id}"
            )
            logger.info(
                f"[Inventory] WiFi networks: {len(fetched_networks)} total,"
                f" {filter_label} (pattern: {name_pattern})"
            )

            name_matched = 0
            venue_matched = 0
            for nw in fetched_networks:
                nw_name = nw.get('name', '')
                if pattern and not pattern.search(nw_name):
                    continue
                name_matched += 1

                # Extract venue IDs from network (always needed for deactivation)
                venues_field = nw.get('venues') or []
                vag_field = nw.get('venueApGroups') or []
                venue_ids = set()
                for v in venues_field:
                    if isinstance(v, dict) and v.get('venueId'):
                        venue_ids.add(v['venueId'])
                for v in vag_field:
                    if isinstance(v, dict) and v.get('venueId'):
                        venue_ids.add(v['venueId'])

                if not all_networks and self.venue_id:
                    # Filter to networks activated on this venue
                    if self.venue_id not in venue_ids:
                        continue
                venue_matched += 1

                inventory.wifi_networks.append({
                    'id': nw.get('id'),
                    'name': nw_name,
                    'ssid': nw.get('ssid', ''),
                    'type': nw.get('nwSubType', ''),
                    'venues': list(venue_ids),
                })
            logger.info(
                f"[Inventory] WiFi networks: {name_matched} matched"
                f" name, {venue_matched} matched venue/included"
            )
        except Exception as e:
            logger.warning(
                f"Failed to query WiFi networks: {e}", exc_info=True
            )

        # Fetch AP groups in this venue (excluding Default group)
        # Only if venue_id is set - AP groups are venue-specific
        if self.venue_id:
            try:
                ag_response = await self.r1_client.venues.query_ap_groups(
                    tenant_id=self.tenant_id,
                    venue_id=self.venue_id,
                    page=1,
                    limit=100,
                )
                ag_items = ag_response.get('data', [])
                ag_total = ag_response.get('totalCount', len(ag_items))

                # Paginate if more pages exist
                if ag_total > len(ag_items):
                    page_size = len(ag_items) or 100
                    pages_needed = (ag_total + page_size - 1) // page_size
                    for page_num in range(2, pages_needed + 1):
                        page_resp = await self.r1_client.venues.query_ap_groups(
                            tenant_id=self.tenant_id,
                            venue_id=self.venue_id,
                            page=page_num,
                            limit=100,
                        )
                        ag_items.extend(page_resp.get('data', []))

                logger.info(
                    f"[Inventory] AP groups: fetched {len(ag_items)}"
                    f" of {ag_total} for venue {self.venue_id}"
                )

                for ag in ag_items:
                    ag_name = ag.get('name', '')
                    # Skip the system Default AP group
                    if ag_name == 'Default':
                        continue
                    if pattern and not pattern.search(ag_name):
                        continue
                    inventory.ap_groups.append({
                        'id': ag.get('id'),
                        'name': ag_name,
                        'venue_id': ag.get('venueId', self.venue_id),
                    })
            except Exception as e:
                logger.warning(f"Failed to query AP groups: {e}", exc_info=True)
        else:
            logger.info("[Inventory] AP groups: skipped (no venue specified)")

        return inventory

    def _from_created_resources(
        self, created_resources: Dict[str, List[Dict[str, Any]]]
    ) -> ResourceInventory:
        """Build inventory from job's created_resources."""
        return ResourceInventory(
            passphrases=created_resources.get('passphrases', []),
            dpsk_pools=created_resources.get('dpsk_pools', []),
            identities=created_resources.get('identities', []),
            identity_groups=created_resources.get(
                'identity_groups', []
            ),
            wifi_networks=created_resources.get('wifi_networks', []),
            ap_groups=created_resources.get('ap_groups', []),
        )


# =============================================================================
# Legacy Adapter for cloudpath_router compatibility
# =============================================================================

async def execute(context: Dict[str, Any]) -> List:
    """
    Legacy adapter for cloudpath workflow execution.

    Converts V1-style context dict to inventory result.

    Args:
        context: V1 workflow context with r1_client, tenant_id, etc.

    Returns:
        List with single Task containing inventory in output_data
    """
    from workflow.v2.models import Task, TaskStatus

    nuclear_mode = context.get('nuclear_mode', False)
    created_resources = context.get('created_resources', {})

    # Build inventory based on mode
    if nuclear_mode:
        r1_client = context.get('r1_client')
        tenant_id = context.get('tenant_id')
        inventory = await _audit_venue_for_cloudpath(r1_client, tenant_id)
    else:
        inventory = {
            'passphrases': created_resources.get('passphrases', []),
            'dpsk_pools': created_resources.get('dpsk_pools', []),
            'identities': created_resources.get('identities', []),
            'identity_groups': created_resources.get('identity_groups', []),
            'policy_sets': created_resources.get('policy_sets', []),
        }

    total_resources = sum(len(items) for items in inventory.values())

    logger.info(f"📋 Inventory: {total_resources} resources")

    task = Task(
        id="inventory_resources",
        name=f"Inventory {total_resources} resources",
        task_type="inventory",
        status=TaskStatus.COMPLETED,
        output_data={
            'inventory': inventory,
            'total_resources': total_resources,
            'nuclear_mode': nuclear_mode
        }
    )

    return [task]


async def _audit_venue_for_cloudpath(r1_client, tenant_id: str) -> Dict[str, List]:
    """Audit venue for DPSK resources (cloudpath legacy adapter)."""
    inventory = {
        'passphrases': [],
        'dpsk_pools': [],
        'identities': [],
        'identity_groups': [],
        'policy_sets': []
    }

    try:
        pools_response = await r1_client.dpsk.query_dpsk_pools(tenant_id=tenant_id)
        pools = (
            pools_response if isinstance(pools_response, list)
            else pools_response.get('content', pools_response.get('data', []))
        )

        for pool in pools:
            pool_id = pool.get('id')
            pool_name = pool.get('name', '')
            identity_group_id = pool.get('identityGroupId')

            inventory['dpsk_pools'].append({'id': pool_id, 'name': pool_name})

            # Fetch passphrases
            try:
                pp_response = await r1_client.dpsk.query_passphrases(
                    pool_id=pool_id, tenant_id=tenant_id, page=1, limit=1000
                )
                passphrases = (
                    pp_response.get('data', pp_response.get('content', []))
                    if isinstance(pp_response, dict) else pp_response
                ) or []
                for pp in passphrases:
                    inventory['passphrases'].append({
                        'id': pp.get('id'),
                        'pool_id': pool_id,
                        'username': pp.get('userName', pp.get('username', pp.get('id'))),
                    })
            except Exception as e:
                logger.warning(f"Failed to fetch passphrases for pool {pool_name}: {e}")

            # Fetch identities
            if identity_group_id:
                try:
                    id_response = await r1_client.identity.get_identities_in_group(
                        group_id=identity_group_id, tenant_id=tenant_id
                    )
                    identities = (
                        id_response.get('content', id_response.get('data', []))
                        if isinstance(id_response, dict) else []
                    ) or []
                    for identity in identities:
                        inventory['identities'].append({
                            'id': identity.get('id'),
                            'pool_id': pool_id,
                            'group_id': identity_group_id,
                            'username': identity.get('userName', identity.get('name', '')),
                        })
                except Exception as e:
                    logger.warning(f"Failed to fetch identities: {e}")

        # Fetch identity groups
        try:
            ig_response = await r1_client.identity.query_identity_groups(tenant_id=tenant_id)
            ig_items = ig_response.get('content', ig_response.get('data', []))
            for ig in ig_items:
                ig_name = ig.get('name', '')
                if 'dpsk' in ig_name.lower() or 'cloudpath' in ig_name.lower():
                    inventory['identity_groups'].append({'id': ig.get('id'), 'name': ig_name})
        except Exception as e:
            logger.warning(f"Failed to query identity groups: {e}")

    except Exception as e:
        logger.exception(f"Error auditing venue: {e}")
        raise

    return inventory
