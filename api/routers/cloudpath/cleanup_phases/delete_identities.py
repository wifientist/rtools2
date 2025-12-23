"""
Delete Identities Phase

Deletes all identity entries from identity groups.
IMPORTANT: Deleting identities will CASCADE-DELETE the associated DPSK passphrases!
"""

import logging
import asyncio
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus
from r1api.services.identity import IdentityService

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Delete identity entries from identity groups

    IMPORTANT: This cascades to delete DPSK passphrases!

    Args:
        context: Workflow context containing inventory and R1 client config

    Returns:
        List of tasks (one per identity deletion)
    """
    logger.info("Phase 2: Delete Identities (cascades to passphrases)")

    # Get inventory from inventory phase
    prev_phase_output = context.get('previous_phase_results', {}).get('inventory_resources', {})
    aggregated = prev_phase_output.get('aggregated', {})
    inventory = aggregated.get('inventory', [{}])[0] if aggregated.get('inventory') else {}
    identities = inventory.get('identities', [])

    if not identities:
        logger.info("No identities to delete")
        return [Task(
            id="delete_identities_none",
            name="No identities to delete",
            task_type="delete_identity",
            status=TaskStatus.COMPLETED
        )]

    logger.info(f"Deleting {len(identities)} identities (will cascade to delete passphrases)")

    # Get context data
    tenant_id = context.get('tenant_id')
    r1_client = context.get('r1_client')

    identity_service = IdentityService(r1_client)

    tasks = []

    # Create a task for each identity deletion
    for identity in identities:
        identity_id = identity.get('id')
        group_id = identity.get('group_id')
        username = identity.get('username', identity_id)

        task_id = f"delete_identity_{identity_id}"

        if not group_id:
            logger.error(f"⚠️ Identity {username} missing group_id - skipping")
            tasks.append(Task(
                id=task_id,
                name=f"Delete identity: {username}",
                task_type="delete_identity",
                status=TaskStatus.FAILED,
                error_message="Missing group_id in inventory data"
            ))
            continue

        try:
            logger.info(f"Deleting identity {username} (ID: {identity_id}) from group {group_id}")

            await identity_service.delete_identity(
                group_id=group_id,
                identity_id=identity_id,
                tenant_id=tenant_id
            )

            tasks.append(Task(
                id=task_id,
                name=f"Delete identity: {username}",
                task_type="delete_identity",
                status=TaskStatus.COMPLETED,
                output_data={
                    'identity_id': identity_id,
                    'group_id': group_id,
                    'username': username
                }
            ))
            logger.info(f"✅ Deleted identity {username} (cascade-deleted passphrase)")

            # Small delay to avoid rate limiting
            if context.get('options', {}).get('simulate_delay'):
                await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"Failed to delete identity {username}: {str(e)}")
            tasks.append(Task(
                id=task_id,
                name=f"Delete identity: {username}",
                task_type="delete_identity",
                status=TaskStatus.FAILED,
                error_message=str(e)
            ))

    successful = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
    failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)

    logger.info(f"Identities deleted: {successful} successful, {failed} failed")
    logger.info(f"💡 NOTE: Deleting identities also CASCADE-DELETED the associated passphrases")

    return tasks
