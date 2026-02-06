"""
V2 Phase: Delete AP Groups

Deletes all AP groups found in the inventory.
Must run AFTER delete_networks (AP groups may still have
SSIDs activated on them).

Runs up to 10 concurrent deletions.
Uses ActivityTracker for bulk polling when available.
"""

import asyncio
import logging
from pydantic import BaseModel, Field
from typing import List

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.phases.cleanup.inventory import ResourceInventory

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 10


@register_phase("delete_ap_groups", "Delete AP Groups")
class DeleteAPGroupsPhase(PhaseExecutor):
    """Delete all inventoried AP groups."""

    class Inputs(BaseModel):
        inventory: ResourceInventory

    class Outputs(BaseModel):
        deleted_count: int = 0
        failed_count: int = 0
        errors: List[str] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        ap_groups = inputs.inventory.ap_groups
        if not ap_groups:
            await self.emit("No AP groups to delete")
            return self.Outputs()

        # Check if we have an activity tracker for bulk polling
        tracker = getattr(self.context, 'activity_tracker', None)
        use_bulk = tracker is not None

        await self.emit(
            f"Deleting {len(ap_groups)} AP groups "
            f"({MAX_CONCURRENT} concurrent, "
            f"{'bulk polling' if use_bulk else 'individual polling'})..."
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def delete_one(ag):
            ag_id = ag.get('id')
            ag_name = ag.get('name', ag_id)
            venue_id = ag.get('venue_id', self.venue_id)

            if not ag_id:
                return (False, ag_name, "missing id")

            async with semaphore:
                # Retry with backoff for eventual consistency
                # AP groups may still have SSIDs attached if network deactivation
                # hasn't fully propagated
                last_error = None
                for attempt in range(4):
                    try:
                        result = await self.r1_client.venues.delete_ap_group(
                            venue_id=venue_id,
                            ap_group_id=ag_id,
                            tenant_id=self.tenant_id,
                            wait_for_completion=not use_bulk,
                        )

                        # Wait via tracker if using bulk polling
                        if use_bulk:
                            req_id = result.get('requestId') if isinstance(result, dict) else None
                            if req_id:
                                await tracker.register(
                                    req_id, self.job_id,
                                    unit_id=None, phase_id=self.phase_id
                                )
                                await tracker.wait(req_id)

                        logger.info(f"Deleted AP group: {ag_name}")
                        return (True, ag_name, None)
                    except Exception as e:
                        if 'not found' in str(e).lower():
                            return (True, ag_name, None)
                        last_error = e
                        if attempt < 3:
                            # Wait for R1 eventual consistency
                            await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s, 9s
                            logger.info(
                                f"AP group {ag_name} retry {attempt + 1}/4..."
                            )

                logger.error(
                    f"Failed to delete AP group {ag_name}: {last_error}"
                )
                return (False, ag_name, str(last_error))

        results = await asyncio.gather(
            *[delete_one(ag) for ag in ap_groups]
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
            f"AP groups: {deleted} deleted, {failed} failed", level
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
    ap_groups = inventory_data.get('ap_groups', [])

    if not ap_groups:
        return [Task(
            id="delete_ap_groups_none",
            name="No AP groups to delete",
            task_type="delete_ap_group",
            status=TaskStatus.COMPLETED
        )]

    # Note: Full implementation would need r1_client from context
    # For cloudpath DPSK cleanup, this phase is typically a no-op
    logger.info(f"AP groups cleanup: {len(ap_groups)} groups (skipped in cloudpath)")

    return [Task(
        id="delete_ap_groups",
        name=f"Skip {len(ap_groups)} AP groups (cloudpath DPSK-only)",
        task_type="delete_ap_group",
        status=TaskStatus.COMPLETED,
        output_data={'deleted': 0, 'skipped': len(ap_groups)}
    )]
