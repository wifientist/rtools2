"""
V2 Phase: Delete DPSK Passphrases

Deletes all passphrases found in the inventory.
Acts as a safety net — identities cascade-delete passphrases,
but this catches any orphaned ones.

Sent in batches per pool, since the route takes a list of IDs. A batch that
fails is retried one at a time, so one bad ID only fails itself.
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
# The most R1 accepts is not documented; this is a cautious first choice.
BATCH_SIZE = 100


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

        by_pool: Dict[str, List[str]] = defaultdict(list)
        errors: List[str] = []
        for pp in passphrases:
            if pp.get('id') and pp.get('pool_id'):
                by_pool[pp['pool_id']].append(pp['id'])
            else:
                errors.append(f"{pp.get('id') or '(no id)'}: missing id or pool_id")

        batches = [
            (pool_id, ids[i:i + BATCH_SIZE])
            for pool_id, ids in by_pool.items()
            for i in range(0, len(ids), BATCH_SIZE)
        ]
        await self.emit(
            f"Deleting {len(passphrases)} passphrases in {len(batches)} "
            f"batches across {len(by_pool)} pools..."
        )

        # One batch at a time per pool: parallel writes to one policy set made
        # R1 refuse them, and nothing yet shows a pool is safer.
        pool_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        deleted = 0
        stopped = False

        async def send(pool_id: str, ids: List[str]) -> None:
            result = await self.r1_client.dpsk.delete_passphrases(
                pool_id=pool_id,
                passphrase_ids=ids,
                tenant_id=self.tenant_id,
            )
            await self.confirm_r1_result(result)

        async def delete_batch(pool_id: str, ids: List[str]) -> None:
            nonlocal deleted, stopped
            if await self.stop_requested():
                stopped = True
                return
            async with pool_locks[pool_id]:
                async with semaphore:
                    try:
                        await send(pool_id, ids)
                        deleted += len(ids)
                    except Exception as batch_error:
                        # One bad ID should not fail its neighbours, so find it.
                        logger.warning(
                            f"Batch of {len(ids)} passphrases failed, trying "
                            f"one at a time: {batch_error}"
                        )
                        for pp_id in ids:
                            try:
                                await send(pool_id, [pp_id])
                                deleted += 1
                            except Exception as e:
                                if 'not found' in str(e).lower():
                                    deleted += 1
                                    continue
                                logger.error(f"Failed to delete passphrase {pp_id}: {e}")
                                errors.append(f"{pp_id}: {e}")
            await self.emit_progress(
                current=deleted + len(errors),
                total=len(passphrases),
                failed=len(errors),
                item_name="passphrase",
            )

        await asyncio.gather(*[delete_batch(p, ids) for p, ids in batches])
        failed = len(errors)

        if stopped:
            await self.emit(
                f"Stopped by request: {deleted} passphrases deleted first", "warning"
            )

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
