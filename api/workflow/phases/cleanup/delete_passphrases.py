"""
V2 Phase: Delete DPSK Passphrases

Deletes all passphrases found in the inventory.
Acts as a safety net — identities cascade-delete passphrases,
but this catches any orphaned ones.

Each passphrase is deleted individually. Runs up to 10 concurrent
deletions.
"""

import asyncio
import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.phases.cleanup.inventory import ResourceInventory

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 10


@register_phase("delete_passphrases", "Delete DPSK Passphrases")
class DeletePassphrasesPhase(PhaseExecutor):
    """Delete all inventoried passphrases."""

    class Inputs(BaseModel):
        inventory: ResourceInventory

    class Outputs(BaseModel):
        deleted_count: int = 0
        failed_count: int = 0
        errors: List[str] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        passphrases = inputs.inventory.passphrases
        if not passphrases:
            await self.emit("No passphrases to delete")
            return self.Outputs()

        await self.emit(
            f"Deleting {len(passphrases)} passphrases "
            f"({MAX_CONCURRENT} concurrent)..."
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def delete_one(pp):
            pp_id = pp.get('id')
            pool_id = pp.get('pool_id')
            username = pp.get('username', pp_id)

            if not pp_id or not pool_id:
                return (False, username, "missing id or pool_id")

            async with semaphore:
                try:
                    await self.r1_client.dpsk.delete_passphrase(
                        passphrase_id=pp_id,
                        pool_id=pool_id,
                        tenant_id=self.tenant_id,
                    )
                    return (True, username, None)
                except Exception as e:
                    if 'not found' in str(e).lower():
                        return (True, username, None)
                    logger.error(
                        f"Failed to delete passphrase {username}: {e}"
                    )
                    return (False, username, str(e))

        results = await asyncio.gather(
            *[delete_one(pp) for pp in passphrases]
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
            f"Passphrases: {deleted} deleted, {failed} failed", level
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
    passphrases = inventory_data.get('passphrases', [])

    if not passphrases:
        return [Task(
            id="delete_passphrases_none",
            name="No passphrases to delete",
            task_type="delete_passphrase",
            status=TaskStatus.COMPLETED
        )]

    r1_client = context.get('r1_client')
    tenant_id = context.get('tenant_id')
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = []

    async def delete_one(pp):
        pp_id = pp.get('id')
        pool_id = pp.get('pool_id')
        username = pp.get('username', pp_id)

        if not pp_id or not pool_id:
            return (False, username, "missing id or pool_id")

        async with semaphore:
            try:
                await r1_client.dpsk.delete_passphrase(
                    passphrase_id=pp_id, pool_id=pool_id, tenant_id=tenant_id
                )
                return (True, username, None)
            except Exception as e:
                if 'not found' in str(e).lower():
                    return (True, username, None)
                return (False, username, str(e))

    results = await asyncio.gather(*[delete_one(pp) for pp in passphrases])

    deleted = sum(1 for ok, _, _ in results if ok)
    failed = sum(1 for ok, _, _ in results if not ok)

    logger.info(f"Passphrases: {deleted} deleted, {failed} failed")

    return [Task(
        id="delete_passphrases",
        name=f"Delete {deleted} passphrases ({failed} failed)",
        task_type="delete_passphrase",
        status=TaskStatus.COMPLETED if failed == 0 else TaskStatus.FAILED,
        output_data={'deleted': deleted, 'failed': failed}
    )]
