"""
V2 Phase: Verify Cleanup

Reads R1 again and reports what the inventory listed that is still there. The
delete phases cannot be the judge: R1 can accept a delete and fail it afterwards.
"""

import asyncio
import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Set

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.phases.cleanup.inventory import InventoryPhase, ResourceInventory

logger = logging.getLogger(__name__)

# R1's reads can lag its deletes, so a leftover gets one more look.
SECOND_LOOK_SECONDS = 20

RESOURCE_TYPES = [
    'passphrases', 'dpsk_pools', 'identities', 'identity_groups', 'wifi_networks',
    'ap_groups', 'policies', 'policy_sets', 'radius_attribute_groups',
]


@register_phase("verify_cleanup", "Verify Cleanup")
class VerifyCleanupPhase(PhaseExecutor):
    """Report what is really left of the inventory."""

    class Inputs(BaseModel):
        inventory: ResourceInventory = Field(default_factory=ResourceInventory)

    class Outputs(BaseModel):
        total_deleted: int = 0
        total_failed: int = 0
        total_skipped: int = 0
        verified: bool = True
        summary: Dict[str, Dict[str, int]] = Field(
            default_factory=dict
        )

    async def _leftovers(self, inventory: ResourceInventory) -> Dict[str, int]:
        current = await InventoryPhase(self.context)._audit_venue(None, True)
        left = {}
        for resource_type in RESOURCE_TYPES:
            wanted: Set[str] = {
                row['id'] for row in getattr(inventory, resource_type) if row.get('id')
            }
            still_there = {
                row['id'] for row in getattr(current, resource_type) if row.get('id')
            }
            left[resource_type] = len(wanted & still_there)
        return left

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        inventory = inputs.inventory
        await self.emit("Checking R1 for anything left behind...")

        try:
            left = await self._leftovers(inventory)
            if any(left.values()):
                await asyncio.sleep(SECOND_LOOK_SECONDS)
                left = await self._leftovers(inventory)
        except Exception as e:
            logger.warning(f"Could not verify cleanup: {e}")
            await self.emit(f"Could not check R1 for leftovers: {e}", "warning")
            return self.Outputs(verified=False)

        summary = {}
        total_deleted = 0
        total_left = 0
        for resource_type in RESOURCE_TYPES:
            listed = len(getattr(inventory, resource_type))
            if not listed:
                continue
            remaining = left[resource_type]
            summary[resource_type] = {'deleted': listed - remaining, 'failed': remaining, 'skipped': 0}
            total_deleted += listed - remaining
            total_left += remaining

        if total_left:
            details = ", ".join(
                f"{counts['failed']} {resource_type.replace('_', ' ')}"
                for resource_type, counts in summary.items() if counts['failed']
            )
            await self.emit(
                f"Cleanup incomplete: {total_deleted} deleted, still in R1: {details}",
                "error",
            )
        else:
            await self.emit(f"Cleanup verified: {total_deleted} deleted, none left", "success")

        return self.Outputs(
            total_deleted=total_deleted,
            total_failed=total_left,
            summary=summary,
        )


# =============================================================================
# Legacy Adapter for cloudpath_router compatibility
# =============================================================================

async def execute(context: Dict[str, Any]) -> List:
    """Legacy adapter for cloudpath workflow execution."""
    from workflow.v2.models import Task, TaskStatus

    # Gather results from previous delete phases
    prev_results = context.get('previous_phase_results', {})

    total_deleted = 0
    total_failed = 0
    summary = {}

    for phase_id, phase_data in prev_results.items():
        if phase_id.startswith('delete_'):
            aggregated = phase_data.get('aggregated', {})
            deleted = sum(aggregated.get('deleted', [0]))
            failed = sum(aggregated.get('failed', [0]))
            total_deleted += deleted
            total_failed += failed
            summary[phase_id] = {'deleted': deleted, 'failed': failed}

    logger.info(f"Cleanup complete: {total_deleted} deleted, {total_failed} failed")

    return [Task(
        id="verify_cleanup",
        name=f"Cleanup: {total_deleted} deleted, {total_failed} failed",
        task_type="verify",
        status=TaskStatus.COMPLETED,
        output_data={
            'total_deleted': total_deleted,
            'total_failed': total_failed,
            'summary': summary
        }
    )]
