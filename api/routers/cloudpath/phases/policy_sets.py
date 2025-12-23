"""
Phase 4: Create Adaptive Policy Sets (Optional)

Creates policy sets for DPSK pools
"""

import logging
import uuid
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Create policy sets

    Args:
        context: Execution context

    Returns:
        List of tasks
    """
    logger.info("Phase 4: Create Adaptive Policy Sets")

    # Get parsed data
    phase1_results = context.get('previous_phase_results', {}).get('parse_validate', {})
    parsed_data = phase1_results.get('aggregated', {}).get('parsed_data', [{}])[0]

    policy_sets = parsed_data.get('policy_sets', [])

    if not policy_sets:
        logger.info("  ℹ️  No policy sets to create")
        return []

    tasks = []
    for ps_data in policy_sets:
        task = Task(
            id=str(uuid.uuid4()),
            name=f"Create Policy Set: {ps_data.get('name', 'Unknown')}",
            status=TaskStatus.PENDING,
            input_data=ps_data
        )
        tasks.append(task)

    logger.info(f"  Generated {len(tasks)} policy set creation tasks")
    return tasks
