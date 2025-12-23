"""
Phase 5: Attach Policy Sets to DPSK Pools (Optional)

Links policy sets to their corresponding DPSK pools
"""

import logging
import uuid
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Attach policy sets to pools

    Args:
        context: Execution context

    Returns:
        List of tasks
    """
    logger.info("Phase 5: Attach Policy Sets to DPSK Pools")

    # Get DPSK pools from Phase 3
    phase3_results = context.get('previous_phase_results', {}).get('create_dpsk_pools', {})
    dpsk_pools = phase3_results.get('task_outputs', [])

    # Get policy sets from Phase 4
    phase4_results = context.get('previous_phase_results', {}).get('create_policy_sets', {})
    policy_sets = phase4_results.get('task_outputs', [])

    if not policy_sets or not dpsk_pools:
        logger.info("  ℹ️  No policy attachments to create")
        return []

    # Create attachment tasks
    tasks = []
    for pool in dpsk_pools:
        # Match pool to policy set (based on naming convention or mapping)
        task = Task(
            id=str(uuid.uuid4()),
            name=f"Attach Policy to Pool: {pool.get('name')}",
            status=TaskStatus.PENDING,
            input_data={
                'dpsk_pool_id': pool.get('dpsk_pool_id'),
                'pool_name': pool.get('name')
            }
        )
        tasks.append(task)

    logger.info(f"  Generated {len(tasks)} policy attachment tasks")
    return tasks
