"""
Delete Identity Groups Phase

Deletes all identity groups.
IMPORTANT: Identity groups can only be deleted if they are not associated with DPSK pools!
Therefore, DPSK pools must be deleted first (in Phase 4).
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus
from r1api.services.identity import IdentityService

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Delete identity groups

    IMPORTANT: This phase must run AFTER deleting DPSK pools!

    Args:
        context: Workflow context containing inventory and R1 client config

    Returns:
        List of tasks (one per identity group deletion)
    """
    logger.info("Phase 5: Delete Identity Groups")

    # Get inventory from inventory phase
    prev_phase_output = context.get('previous_phase_results', {}).get('inventory_resources', {})
    # Inventory is wrapped in a list by the workflow engine's aggregation
    aggregated = prev_phase_output.get('aggregated', {})
    inventory = aggregated.get('inventory', [{}])[0] if aggregated.get('inventory') else {}
    identity_groups = inventory.get('identity_groups', [])

    if not identity_groups:
        logger.info("No identity groups to delete")
        return [Task(
            id="delete_identity_groups_none",
            name="No identity groups to delete",
            task_type="delete_identity_group",
            status=TaskStatus.COMPLETED
        )]

    logger.info(f"Deleting {len(identity_groups)} identity groups")

    # Get context data
    tenant_id = context.get('tenant_id')
    r1_client = context.get('r1_client')

    identity_service = IdentityService(r1_client)

    tasks = []

    # Create a task for each identity group deletion
    for group in identity_groups:
        group_id = group.get('id')
        group_name = group.get('name', group_id)

        task_id = f"delete_identity_group_{group_id}"

        try:
            logger.info(f"Deleting identity group {group_name} (ID: {group_id})")

            await identity_service.delete_identity_group(
                group_id=group_id,
                tenant_id=tenant_id
            )

            tasks.append(Task(
                id=task_id,
                name=f"Delete identity group: {group_name}",
                task_type="delete_identity_group",
                status=TaskStatus.COMPLETED,
                output_data={'group_id': group_id, 'group_name': group_name}
            ))
            logger.info(f"✅ Deleted identity group {group_name}")

        except Exception as e:
            logger.error(f"Failed to delete identity group {group_name}: {str(e)}")
            tasks.append(Task(
                id=task_id,
                name=f"Delete identity group: {group_name}",
                task_type="delete_identity_group",
                status=TaskStatus.FAILED,
                error_message=str(e)
            ))

    successful = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
    failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)

    logger.info(f"Identity groups deleted: {successful} successful, {failed} failed")

    return tasks
