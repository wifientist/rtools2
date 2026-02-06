"""
V2 Phase: Delete Identities

Deletes all identities found in the inventory from their
identity groups. Must run AFTER delete_dpsk_pools and
BEFORE delete_identity_groups (groups must be empty to delete).

Identities are grouped by identity group and bulk-deleted per group.
Groups are processed concurrently (up to 10 at a time).
Uses ActivityTracker for bulk polling when available.
"""

import asyncio
import logging
from collections import defaultdict
from pydantic import BaseModel, Field
from typing import List, Dict, Any

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.phases.cleanup.inventory import ResourceInventory

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 10


@register_phase("delete_identities", "Delete Identities")
class DeleteIdentitiesPhase(PhaseExecutor):
    """Delete all inventoried identities from their identity groups."""

    class Inputs(BaseModel):
        inventory: ResourceInventory

    class Outputs(BaseModel):
        deleted_count: int = 0
        failed_count: int = 0
        errors: List[str] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        identities = inputs.inventory.identities
        if not identities:
            await self.emit("No identities to delete")
            return self.Outputs()

        # Group identities by their identity group for bulk deletion
        by_group: Dict[str, List[str]] = defaultdict(list)
        for identity in identities:
            group_id = identity.get('group_id')
            identity_id = identity.get('id')
            if group_id and identity_id:
                by_group[group_id].append(identity_id)

        # Check if we have an activity tracker for bulk polling
        tracker = getattr(self.context, 'activity_tracker', None)
        use_bulk = tracker is not None

        await self.emit(
            f"Deleting {len(identities)} identities "
            f"across {len(by_group)} groups "
            f"({MAX_CONCURRENT} concurrent, "
            f"{'bulk polling' if use_bulk else 'individual polling'})..."
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def delete_group(group_id, identity_ids):
            async with semaphore:
                try:
                    result = await self.r1_client.identity.delete_identities_bulk(
                        group_id=group_id,
                        identity_ids=identity_ids,
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

                    logger.info(
                        f"Deleted {len(identity_ids)} identities "
                        f"from group {group_id}"
                    )
                    return (True, len(identity_ids), group_id, None)
                except Exception as e:
                    if 'not found' in str(e).lower():
                        return (True, len(identity_ids), group_id, None)
                    logger.error(
                        f"Failed to delete identities from group "
                        f"{group_id}: {e}"
                    )
                    return (
                        False, len(identity_ids), group_id, str(e)
                    )

        results = await asyncio.gather(
            *[
                delete_group(gid, ids)
                for gid, ids in by_group.items()
            ]
        )

        deleted = sum(count for ok, count, _, _ in results if ok)
        failed = sum(count for ok, count, _, _ in results if not ok)
        errors = [
            f"group {gid} ({count} identities): {err}"
            for ok, count, gid, err in results
            if not ok and err
        ]

        level = "success" if failed == 0 else "warning"
        await self.emit(
            f"Identities: {deleted} deleted, {failed} failed", level
        )

        return self.Outputs(
            deleted_count=deleted,
            failed_count=failed,
            errors=errors,
        )


# =============================================================================
# Legacy Adapter for cloudpath_router compatibility
# =============================================================================

async def execute(context: Dict[str, Any]) -> List:
    """Legacy adapter for cloudpath workflow execution."""
    from workflow.v2.models import Task, TaskStatus

    # Get inventory from previous phase
    prev_results = context.get('previous_phase_results', {})
    inv_result = prev_results.get('inventory', prev_results.get('inventory_resources', {}))
    aggregated = inv_result.get('aggregated', {})
    inventory_data = aggregated.get('inventory', [{}])[0] if aggregated.get('inventory') else inv_result.get('inventory', {})
    identities = inventory_data.get('identities', [])

    if not identities:
        return [Task(
            id="delete_identities_none",
            name="No identities to delete",
            task_type="delete_identity",
            status=TaskStatus.COMPLETED
        )]

    r1_client = context.get('r1_client')
    tenant_id = context.get('tenant_id')

    # Group by identity group for bulk deletion
    by_group: Dict[str, List[str]] = defaultdict(list)
    for identity in identities:
        group_id = identity.get('group_id')
        identity_id = identity.get('id')
        if group_id and identity_id:
            by_group[group_id].append(identity_id)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def delete_group(group_id, identity_ids):
        async with semaphore:
            try:
                await r1_client.identity.delete_identities_bulk(
                    group_id=group_id,
                    identity_ids=identity_ids,
                    tenant_id=tenant_id,
                    wait_for_completion=True,
                )
                logger.info(f"Deleted {len(identity_ids)} identities from group {group_id}")
                return (True, len(identity_ids), group_id, None)
            except Exception as e:
                if 'not found' in str(e).lower():
                    return (True, len(identity_ids), group_id, None)
                return (False, len(identity_ids), group_id, str(e))

    results = await asyncio.gather(*[
        delete_group(gid, ids) for gid, ids in by_group.items()
    ])

    deleted = sum(count for ok, count, _, _ in results if ok)
    failed = sum(count for ok, count, _, _ in results if not ok)

    logger.info(f"Identities: {deleted} deleted, {failed} failed")

    return [Task(
        id="delete_identities",
        name=f"Delete {deleted} identities ({failed} failed)",
        task_type="delete_identity",
        status=TaskStatus.COMPLETED if failed == 0 else TaskStatus.FAILED,
        output_data={'deleted': deleted, 'failed': failed}
    )]
