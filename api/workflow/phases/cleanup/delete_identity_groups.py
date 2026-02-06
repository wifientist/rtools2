"""
V2 Phase: Delete Identity Groups

Deletes all identity groups found in the inventory.
Must run AFTER delete_identities (groups must be empty to delete)
and AFTER delete_dpsk_pools (identity groups can't be deleted
while a DPSK pool references them).

Runs up to 10 concurrent deletions.
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


@register_phase(
    "delete_identity_groups", "Delete Identity Groups"
)
class DeleteIdentityGroupsPhase(PhaseExecutor):
    """Delete all inventoried identity groups."""

    class Inputs(BaseModel):
        inventory: ResourceInventory

    class Outputs(BaseModel):
        deleted_count: int = 0
        failed_count: int = 0
        errors: List[str] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        groups = inputs.inventory.identity_groups
        if not groups:
            await self.emit("No identity groups to delete")
            return self.Outputs()

        await self.emit(
            f"Deleting {len(groups)} identity groups "
            f"({MAX_CONCURRENT} concurrent)..."
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def delete_one(group):
            group_id = group.get('id')
            group_name = group.get('name', group_id)

            if not group_id:
                return (False, group_name, "missing id")

            async with semaphore:
                try:
                    await self.r1_client.identity.delete_identity_group(
                        group_id=group_id,
                        tenant_id=self.tenant_id,
                    )
                    logger.info(
                        f"Deleted identity group: {group_name}"
                    )
                    return (True, group_name, None)
                except Exception as e:
                    error_str = str(e).lower()
                    if 'not found' in error_str:
                        return (True, group_name, None)

                    # GENERAL-026: Group has DPSK pools attached
                    if 'general-026' in error_str or 'associated with a dpsk pool' in error_str:
                        logger.warning(
                            f"Identity group {group_name} has DPSK pools, "
                            f"cleaning up before retry..."
                        )
                        try:
                            # Query DPSK pools for this identity group
                            pools_response = await self.r1_client.dpsk.query_dpsk_pools(
                                tenant_id=self.tenant_id
                            )
                            pools = (
                                pools_response if isinstance(pools_response, list)
                                else pools_response.get('content', pools_response.get('data', []))
                            )

                            # Find pools linked to this identity group
                            for pool in pools:
                                if pool.get('identityGroupId') == group_id:
                                    pool_id = pool.get('id')
                                    pool_name = pool.get('name', pool_id)
                                    logger.info(f"  Deleting linked pool: {pool_name}")

                                    # Delete passphrases in pool first
                                    try:
                                        pp_response = await self.r1_client.dpsk.query_passphrases(
                                            pool_id=pool_id,
                                            tenant_id=self.tenant_id,
                                            page=1,
                                            limit=1000,
                                        )
                                        passphrases = pp_response.get('data', []) if isinstance(pp_response, dict) else []
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
                                                    pass
                                    except Exception:
                                        pass

                                    # Delete the pool
                                    try:
                                        await self.r1_client.dpsk.delete_dpsk_pool(
                                            pool_id=pool_id,
                                            tenant_id=self.tenant_id,
                                        )
                                        # Wait for pool deletion to propagate
                                        await asyncio.sleep(3)
                                    except Exception:
                                        pass

                            # Wait for R1 eventual consistency, then retry with backoff
                            # Longer delays since pool deletions take time to propagate
                            for attempt in range(4):
                                await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s, 9s, 12s
                                try:
                                    await self.r1_client.identity.delete_identity_group(
                                        group_id=group_id,
                                        tenant_id=self.tenant_id,
                                    )
                                    logger.info(
                                        f"Deleted identity group: {group_name} "
                                        f"(after cleaning linked pools)"
                                    )
                                    return (True, group_name, None)
                                except Exception as inner_e:
                                    if attempt < 3:
                                        logger.info(
                                            f"Identity group {group_name} retry "
                                            f"{attempt + 1}/4..."
                                        )
                                    else:
                                        raise inner_e
                        except Exception as retry_err:
                            logger.error(
                                f"Failed to delete identity group {group_name} "
                                f"after cleanup: {retry_err}"
                            )
                            return (False, group_name, str(retry_err))

                    logger.error(
                        f"Failed to delete identity group "
                        f"{group_name}: {e}"
                    )
                    return (False, group_name, str(e))

        results = await asyncio.gather(
            *[delete_one(group) for group in groups]
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
            f"Identity groups: {deleted} deleted, {failed} failed",
            level,
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
    groups = inventory_data.get('identity_groups', [])

    if not groups:
        return [Task(
            id="delete_identity_groups_none",
            name="No identity groups to delete",
            task_type="delete_identity_group",
            status=TaskStatus.COMPLETED
        )]

    r1_client = context.get('r1_client')
    tenant_id = context.get('tenant_id')
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def delete_one(group):
        group_id = group.get('id')
        group_name = group.get('name', group_id)

        if not group_id:
            return (False, group_name, "missing id")

        async with semaphore:
            try:
                await r1_client.identity.delete_identity_group(
                    group_id=group_id, tenant_id=tenant_id
                )
                logger.info(f"Deleted identity group: {group_name}")
                return (True, group_name, None)
            except Exception as e:
                error_str = str(e).lower()
                if 'not found' in error_str:
                    return (True, group_name, None)

                # GENERAL-026: Group has DPSK pools attached - clean up first
                if 'general-026' in error_str or 'associated with a dpsk pool' in error_str:
                    logger.warning(f"Identity group {group_name} has DPSK pools, cleaning up...")
                    try:
                        pools_response = await r1_client.dpsk.query_dpsk_pools(tenant_id=tenant_id)
                        pools = (
                            pools_response if isinstance(pools_response, list)
                            else pools_response.get('content', pools_response.get('data', []))
                        )
                        for pool in pools:
                            if pool.get('identityGroupId') == group_id:
                                pool_id = pool.get('id')
                                # Delete passphrases first
                                try:
                                    pp_response = await r1_client.dpsk.query_passphrases(
                                        pool_id=pool_id, tenant_id=tenant_id, page=1, limit=1000
                                    )
                                    for pp in pp_response.get('data', []):
                                        try:
                                            await r1_client.dpsk.delete_passphrase(
                                                passphrase_id=pp.get('id'), pool_id=pool_id, tenant_id=tenant_id
                                            )
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                                # Delete pool
                                try:
                                    await r1_client.dpsk.delete_dpsk_pool(pool_id=pool_id, tenant_id=tenant_id)
                                    await asyncio.sleep(3)  # Wait for pool deletion to propagate
                                except Exception:
                                    pass
                        # Wait for R1 eventual consistency, then retry with backoff
                        for attempt in range(4):
                            await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s, 9s, 12s
                            try:
                                await r1_client.identity.delete_identity_group(group_id=group_id, tenant_id=tenant_id)
                                logger.info(f"Deleted identity group: {group_name} (after cleanup)")
                                return (True, group_name, None)
                            except Exception as inner_e:
                                if attempt == 3:
                                    raise inner_e
                    except Exception as retry_err:
                        return (False, group_name, str(retry_err))

                return (False, group_name, str(e))

    results = await asyncio.gather(*[delete_one(group) for group in groups])

    deleted = sum(1 for ok, _, _ in results if ok)
    failed = sum(1 for ok, _, _ in results if not ok)

    logger.info(f"Identity groups: {deleted} deleted, {failed} failed")

    return [Task(
        id="delete_identity_groups",
        name=f"Delete {deleted} identity groups ({failed} failed)",
        task_type="delete_identity_group",
        status=TaskStatus.COMPLETED if failed == 0 else TaskStatus.FAILED,
        output_data={'deleted': deleted, 'failed': failed}
    )]
