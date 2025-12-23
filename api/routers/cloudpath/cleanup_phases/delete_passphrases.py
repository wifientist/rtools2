"""
Delete Passphrases Phase

Deletes all DPSK passphrases from their pools.
"""

import logging
import asyncio
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus
from r1api.services.dpsk import DpskService

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Delete all DPSK passphrases from their pools

    Args:
        context: Workflow context containing inventory and R1 client config

    Returns:
        List of tasks (one per passphrase deletion)
    """
    logger.info("Phase 2: Delete DPSK Passphrases")

    # Get inventory from previous phase
    prev_phase_output = context.get('previous_phase_results', {}).get('inventory_resources', {})
    # Inventory is wrapped in a list by the workflow engine's aggregation
    aggregated = prev_phase_output.get('aggregated', {})
    inventory = aggregated.get('inventory', [{}])[0] if aggregated.get('inventory') else {}
    passphrases = inventory.get('passphrases', [])

    if not passphrases:
        logger.info("No passphrases to delete")
        return [Task(
            id="delete_passphrases_none",
            name="No passphrases to delete",
            task_type="delete_passphrase",
            status=TaskStatus.COMPLETED
        )]

    logger.info(f"Deleting {len(passphrases)} passphrases")

    # Get context data
    tenant_id = context.get('tenant_id')
    r1_client = context.get('r1_client')

    dpsk_service = DpskService(r1_client)

    tasks = []

    # Create a task for each passphrase deletion
    for passphrase in passphrases:
        passphrase_id = passphrase.get('id')
        pool_id = passphrase.get('pool_id')
        username = passphrase.get('username', passphrase_id)

        task_id = f"delete_passphrase_{passphrase_id}"

        try:
            logger.info(f"Deleting passphrase {username} (ID: {passphrase_id}) from pool {pool_id}")

            await dpsk_service.delete_passphrase(
                passphrase_id=passphrase_id,
                pool_id=pool_id,
                tenant_id=tenant_id
            )

            tasks.append(Task(
                id=task_id,
                name=f"Delete passphrase: {username}",
                task_type="delete_passphrase",
                status=TaskStatus.COMPLETED,
                output_data={'passphrase_id': passphrase_id, 'pool_id': pool_id}
            ))
            logger.info(f"✅ Deleted passphrase {username}")

            # Small delay to avoid rate limiting
            if context.get('options', {}).get('simulate_delay'):
                await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"Failed to delete passphrase {username}: {str(e)}")
            tasks.append(Task(
                id=task_id,
                name=f"Delete passphrase: {username}",
                task_type="delete_passphrase",
                status=TaskStatus.FAILED,
                error_message=str(e)
            ))

    successful = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
    failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)

    logger.info(f"Passphrases deleted: {successful} successful, {failed} failed")

    return tasks
