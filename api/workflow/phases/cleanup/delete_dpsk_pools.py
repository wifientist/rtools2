"""
V2 Phase: Delete DPSK Pools

Deletes all DPSK pools (services) found in the inventory.
Must run AFTER delete_passphrases (pools can't be deleted while
passphrases reference them).

Each pool is deleted individually. Runs up to 10 concurrent deletions.
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


@register_phase("delete_dpsk_pools", "Delete DPSK Pools")
class DeleteDPSKPoolsPhase(PhaseExecutor):
    """Delete all inventoried DPSK pools."""

    class Inputs(BaseModel):
        inventory: ResourceInventory

    class Outputs(BaseModel):
        deleted_count: int = 0
        failed_count: int = 0
        errors: List[str] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        pools = inputs.inventory.dpsk_pools
        if not pools:
            await self.emit("No DPSK pools to delete")
            return self.Outputs()

        await self.emit(
            f"Deleting {len(pools)} DPSK pools "
            f"({MAX_CONCURRENT} concurrent)..."
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def delete_one(pool):
            pool_id = pool.get('id')
            pool_name = pool.get('name', pool_id)

            if not pool_id:
                return (False, pool_name, "missing id")

            async with semaphore:
                try:
                    await self.r1_client.dpsk.delete_dpsk_pool(
                        pool_id=pool_id,
                        tenant_id=self.tenant_id,
                    )
                    logger.info(f"Deleted DPSK pool: {pool_name}")
                    return (True, pool_name, None)
                except Exception as e:
                    error_str = str(e).lower()
                    if 'not found' in error_str:
                        return (True, pool_name, None)

                    # DPSK-026: Pool still has passphrases - delete them first
                    if 'dpsk-026' in error_str or 'contains passphrases' in error_str:
                        logger.warning(
                            f"Pool {pool_name} has remaining passphrases, "
                            f"cleaning up before retry..."
                        )
                        try:
                            # Query all passphrases in this pool
                            pp_response = await self.r1_client.dpsk.query_passphrases(
                                pool_id=pool_id,
                                tenant_id=self.tenant_id,
                                page=1,
                                limit=1000,
                            )
                            passphrases = pp_response.get('data', []) if isinstance(pp_response, dict) else []

                            # Delete each passphrase
                            for pp in passphrases:
                                pp_id = pp.get('id')
                                if pp_id:
                                    try:
                                        await self.r1_client.dpsk.delete_passphrase(
                                            passphrase_id=pp_id,
                                            pool_id=pool_id,
                                            tenant_id=self.tenant_id,
                                        )
                                    except Exception:
                                        pass  # Best effort

                            # Wait for R1 eventual consistency, then retry with backoff
                            for attempt in range(4):
                                await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s, 9s, 12s
                                try:
                                    await self.r1_client.dpsk.delete_dpsk_pool(
                                        pool_id=pool_id,
                                        tenant_id=self.tenant_id,
                                    )
                                    logger.info(
                                        f"Deleted DPSK pool: {pool_name} "
                                        f"(after cleaning {len(passphrases)} passphrases)"
                                    )
                                    return (True, pool_name, None)
                                except Exception as inner_e:
                                    if attempt < 3:
                                        logger.info(
                                            f"Pool {pool_name} retry {attempt + 1}/4..."
                                        )
                                    else:
                                        raise inner_e
                        except Exception as retry_err:
                            logger.error(
                                f"Failed to delete pool {pool_name} after cleanup: {retry_err}"
                            )
                            return (False, pool_name, str(retry_err))

                    logger.error(
                        f"Failed to delete DPSK pool {pool_name}: {e}"
                    )
                    return (False, pool_name, str(e))

        results = await asyncio.gather(
            *[delete_one(pool) for pool in pools]
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
            f"DPSK pools: {deleted} deleted, {failed} failed", level
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
    pools = inventory_data.get('dpsk_pools', [])

    if not pools:
        return [Task(
            id="delete_dpsk_pools_none",
            name="No DPSK pools to delete",
            task_type="delete_dpsk_pool",
            status=TaskStatus.COMPLETED
        )]

    r1_client = context.get('r1_client')
    tenant_id = context.get('tenant_id')
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def delete_one(pool):
        pool_id = pool.get('id')
        pool_name = pool.get('name', pool_id)

        if not pool_id:
            return (False, pool_name, "missing id")

        async with semaphore:
            try:
                await r1_client.dpsk.delete_dpsk_pool(pool_id=pool_id, tenant_id=tenant_id)
                logger.info(f"Deleted DPSK pool: {pool_name}")
                return (True, pool_name, None)
            except Exception as e:
                error_str = str(e).lower()
                if 'not found' in error_str:
                    return (True, pool_name, None)

                # DPSK-026: Pool still has passphrases - delete them first
                if 'dpsk-026' in error_str or 'contains passphrases' in error_str:
                    logger.warning(f"Pool {pool_name} has remaining passphrases, cleaning up...")
                    try:
                        pp_response = await r1_client.dpsk.query_passphrases(
                            pool_id=pool_id, tenant_id=tenant_id, page=1, limit=1000
                        )
                        passphrases = pp_response.get('data', []) if isinstance(pp_response, dict) else []
                        for pp in passphrases:
                            pp_id = pp.get('id')
                            if pp_id:
                                try:
                                    await r1_client.dpsk.delete_passphrase(
                                        passphrase_id=pp_id, pool_id=pool_id, tenant_id=tenant_id
                                    )
                                except Exception:
                                    pass
                        # Wait for R1 eventual consistency, then retry with backoff
                        for attempt in range(4):
                            await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s, 9s, 12s
                            try:
                                await r1_client.dpsk.delete_dpsk_pool(pool_id=pool_id, tenant_id=tenant_id)
                                logger.info(f"Deleted DPSK pool: {pool_name} (after cleanup)")
                                return (True, pool_name, None)
                            except Exception as inner_e:
                                if attempt == 3:
                                    raise inner_e
                    except Exception as retry_err:
                        return (False, pool_name, str(retry_err))

                return (False, pool_name, str(e))

    results = await asyncio.gather(*[delete_one(pool) for pool in pools])

    deleted = sum(1 for ok, _, _ in results if ok)
    failed = sum(1 for ok, _, _ in results if not ok)

    logger.info(f"DPSK pools: {deleted} deleted, {failed} failed")

    return [Task(
        id="delete_dpsk_pools",
        name=f"Delete {deleted} DPSK pools ({failed} failed)",
        task_type="delete_dpsk_pool",
        status=TaskStatus.COMPLETED if failed == 0 else TaskStatus.FAILED,
        output_data={'deleted': deleted, 'failed': failed}
    )]
