"""
Delete DPSK Pools Phase

Deletes all DPSK pools (services).
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus
from r1api.services.dpsk import DpskService

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Delete all DPSK pools

    Args:
        context: Workflow context containing inventory and R1 client config

    Returns:
        List of tasks (one per DPSK pool deletion)
    """
    logger.info("Phase 3: Delete DPSK Pools")

    # Get inventory from inventory phase
    prev_phase_output = context.get('previous_phase_results', {}).get('inventory_resources', {})
    # Inventory is wrapped in a list by the workflow engine's aggregation
    aggregated = prev_phase_output.get('aggregated', {})
    inventory = aggregated.get('inventory', [{}])[0] if aggregated.get('inventory') else {}
    dpsk_pools = inventory.get('dpsk_pools', [])

    if not dpsk_pools:
        logger.info("No DPSK pools to delete")
        return [Task(
            id="delete_dpsk_pools_none",
            name="No DPSK pools to delete",
            task_type="delete_dpsk_pool",
            status=TaskStatus.COMPLETED
        )]

    logger.info(f"Deleting {len(dpsk_pools)} DPSK pools")

    # Get context data
    tenant_id = context.get('tenant_id')
    r1_client = context.get('r1_client')

    dpsk_service = DpskService(r1_client)

    tasks = []

    # Create a task for each DPSK pool deletion
    for pool in dpsk_pools:
        pool_id = pool.get('id')
        pool_name = pool.get('name', pool_id)

        task_id = f"delete_dpsk_pool_{pool_id}"

        try:
            logger.info(f"Deleting DPSK pool {pool_name} (ID: {pool_id})")

            await dpsk_service.delete_dpsk_pool(
                pool_id=pool_id,
                tenant_id=tenant_id
            )

            tasks.append(Task(
                id=task_id,
                name=f"Delete DPSK pool: {pool_name}",
                task_type="delete_dpsk_pool",
                status=TaskStatus.COMPLETED,
                output_data={'pool_id': pool_id, 'pool_name': pool_name}
            ))
            logger.info(f"✅ Deleted DPSK pool {pool_name}")

        except Exception as e:
            logger.error(f"Failed to delete DPSK pool {pool_name}: {str(e)}")
            tasks.append(Task(
                id=task_id,
                name=f"Delete DPSK pool: {pool_name}",
                task_type="delete_dpsk_pool",
                status=TaskStatus.FAILED,
                error_message=str(e)
            ))

    successful = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
    failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)

    logger.info(f"DPSK pools deleted: {successful} successful, {failed} failed")

    return tasks
