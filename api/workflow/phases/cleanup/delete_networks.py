"""
V2 Phase: Delete WiFi Networks

Deletes all WiFi networks found in the inventory.
Must run AFTER delete_identity_groups (networks may reference
DPSK services).

Each network is deactivated from ALL its venues (DELETE per venue)
then the network itself is deleted. Runs up to 5 concurrent deletions.

Uses ActivityTracker for bulk polling when available - dispatches all
deactivation/deletion requests, then waits for completion in batches
instead of polling each activity individually.
"""

import asyncio
import logging
from pydantic import BaseModel, Field
from typing import List

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.phases.cleanup.inventory import ResourceInventory

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 5  # Each network may need multiple venue deactivations


@register_phase("delete_networks", "Delete WiFi Networks")
class DeleteNetworksPhase(PhaseExecutor):
    """Delete all inventoried WiFi networks."""

    class Inputs(BaseModel):
        inventory: ResourceInventory

    class Outputs(BaseModel):
        deleted_count: int = 0
        failed_count: int = 0
        errors: List[str] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        networks = inputs.inventory.wifi_networks
        if not networks:
            await self.emit("No WiFi networks to delete")
            return self.Outputs()

        # Check if we have an activity tracker for bulk polling
        tracker = getattr(self.context, 'activity_tracker', None)
        use_bulk = tracker is not None

        await self.emit(
            f"Deleting {len(networks)} WiFi networks "
            f"({MAX_CONCURRENT} concurrent, "
            f"{'bulk polling' if use_bulk else 'individual polling'})..."
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def delete_one(nw):
            nw_id = nw.get('id')
            nw_name = nw.get('name', nw_id)
            venues = nw.get('venues') or []

            if not nw_id:
                return (False, nw_name, "missing id")

            async with semaphore:
                try:
                    # Phase 1: Dispatch all venue deactivations
                    deactivation_request_ids = []
                    if venues:
                        logger.info(
                            f"Deactivating '{nw_name}' from "
                            f"{len(venues)} venue(s)"
                        )
                        for venue_id in venues:
                            try:
                                result = await self.r1_client.venues.deactivate_ssid_from_venue(
                                    tenant_id=self.tenant_id,
                                    venue_id=venue_id,
                                    wifi_network_id=nw_id,
                                    wait_for_completion=not use_bulk,
                                )
                                # Collect requestId for bulk tracking
                                if use_bulk:
                                    req_id = result.get('requestId') if isinstance(result, dict) else None
                                    if req_id:
                                        deactivation_request_ids.append(req_id)
                            except Exception as e:
                                if 'not found' not in str(e).lower():
                                    logger.warning(
                                        f"Failed to deactivate '{nw_name}' "
                                        f"from venue {venue_id}: {e}"
                                    )
                                # Continue with other venues
                    else:
                        # No venues in inventory - fetch and deactivate
                        # (fallback for legacy inventory data)
                        await self.r1_client.networks.deactivate_from_all_venues(
                            network_id=nw_id,
                            tenant_id=self.tenant_id,
                        )

                    # Phase 2: Wait for all deactivations to complete (bulk)
                    if use_bulk and deactivation_request_ids:
                        logger.debug(
                            f"Waiting for {len(deactivation_request_ids)} "
                            f"deactivations via bulk tracker"
                        )
                        for req_id in deactivation_request_ids:
                            await tracker.register(
                                req_id, self.job_id,
                                unit_id=None, phase_id=self.phase_id
                            )
                        await tracker.wait_batch(deactivation_request_ids)

                    # Phase 3: Delete the network itself (with retry for consistency)
                    last_error = None
                    for attempt in range(4):
                        try:
                            # Delay for R1 eventual consistency after deactivations
                            if attempt > 0 or deactivation_request_ids:
                                await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s, 9s, 12s

                            delete_result = await self.r1_client.networks.delete_wifi_network(
                                network_id=nw_id,
                                tenant_id=self.tenant_id,
                                wait_for_completion=not use_bulk,
                            )

                            # Phase 4: Wait for deletion (bulk)
                            if use_bulk:
                                delete_req_id = delete_result.get('requestId') if isinstance(delete_result, dict) else None
                                if delete_req_id:
                                    await tracker.register(
                                        delete_req_id, self.job_id,
                                        unit_id=None, phase_id=self.phase_id
                                    )
                                    await tracker.wait(delete_req_id)

                            logger.info(f"Deleted WiFi network: {nw_name}")
                            return (True, nw_name, None)
                        except Exception as e:
                            if 'not found' in str(e).lower():
                                return (True, nw_name, None)
                            last_error = e
                            if attempt < 3:
                                logger.info(
                                    f"Network {nw_name} retry {attempt + 1}/4..."
                                )

                    logger.error(
                        f"Failed to delete WiFi network {nw_name}: {last_error}"
                    )
                    return (False, nw_name, str(last_error))
                except Exception as e:
                    # Catch errors from deactivation phases
                    if 'not found' in str(e).lower():
                        return (True, nw_name, None)
                    logger.error(
                        f"Failed to delete WiFi network {nw_name}: {e}"
                    )
                    return (False, nw_name, str(e))

        results = await asyncio.gather(
            *[delete_one(nw) for nw in networks]
        )

        deleted = sum(1 for ok, _, _ in results if ok)
        failed = sum(1 for ok, _, _ in results if not ok)
        errors = [
            f"{name}: {err}"
            for ok, name, err in results
            if not ok and err
        ]

        level = "success" if failed == 0 else "warning"
        await self.emit(
            f"WiFi networks: {deleted} deleted, {failed} failed", level
        )

        return self.Outputs(
            deleted_count=deleted,
            failed_count=failed,
            errors=errors,
        )


# =============================================================================
# Legacy Adapter for cloudpath_router compatibility
# =============================================================================

async def execute(context: dict) -> List:
    """Legacy adapter for cloudpath workflow execution."""
    from workflow.v2.models import Task, TaskStatus

    # Get inventory from previous phase
    prev_results = context.get('previous_phase_results', {})
    inv_result = prev_results.get('inventory', prev_results.get('inventory_resources', {}))
    aggregated = inv_result.get('aggregated', {})
    inventory_data = aggregated.get('inventory', [{}])[0] if aggregated.get('inventory') else inv_result.get('inventory', {})
    networks = inventory_data.get('wifi_networks', [])

    if not networks:
        return [Task(
            id="delete_networks_none",
            name="No WiFi networks to delete",
            task_type="delete_network",
            status=TaskStatus.COMPLETED
        )]

    # Note: Full implementation would need r1_client from context
    # For cloudpath DPSK cleanup, this phase is typically a no-op
    logger.info(f"WiFi networks cleanup: {len(networks)} networks (skipped in cloudpath)")

    return [Task(
        id="delete_networks",
        name=f"Skip {len(networks)} WiFi networks (cloudpath DPSK-only)",
        task_type="delete_network",
        status=TaskStatus.COMPLETED,
        output_data={'deleted': 0, 'skipped': len(networks)}
    )]
