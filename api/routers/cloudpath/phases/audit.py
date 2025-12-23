"""
Phase 8: Audit Results

Audits all created resources and generates summary report
"""

import logging
import uuid
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Audit created resources

    Args:
        context: Execution context with all previous phase results

    Returns:
        List of tasks (single audit task)
    """
    logger.info("Phase 8: Audit Results")

    # Create single audit task
    task = Task(
        id=str(uuid.uuid4()),
        name="Audit Created Resources",
        status=TaskStatus.PENDING,
        input_data={}
    )

    try:
        # Collect results from all phases
        phase2_results = context.get('previous_phase_results', {}).get('create_identity_groups', {})
        phase3_results = context.get('previous_phase_results', {}).get('create_dpsk_pools', {})
        phase6_results = context.get('previous_phase_results', {}).get('create_passphrases', {})

        identity_groups = phase2_results.get('task_outputs', [])
        dpsk_pools = phase3_results.get('task_outputs', [])
        passphrases = phase6_results.get('task_outputs', [])

        # Calculate statistics
        summary = {
            'identity_groups': {
                'total': len(identity_groups),
                'created': len([ig for ig in identity_groups if ig.get('created')]),
                'existed': len([ig for ig in identity_groups if ig.get('existed')])
            },
            'dpsk_pools': {
                'total': len(dpsk_pools),
                'created': len([p for p in dpsk_pools if p.get('created')]),
                'existed': len([p for p in dpsk_pools if p.get('existed')])
            },
            'passphrases': {
                'total': len(passphrases),
                'created': len([pp for pp in passphrases if pp.get('created')])
            }
        }

        logger.info(f"  📊 Audit Summary:")
        logger.info(f"    Identity Groups: {summary['identity_groups']['total']} "
                   f"({summary['identity_groups']['created']} created, "
                   f"{summary['identity_groups']['existed']} existed)")
        logger.info(f"    DPSK Pools: {summary['dpsk_pools']['total']} "
                   f"({summary['dpsk_pools']['created']} created, "
                   f"{summary['dpsk_pools']['existed']} existed)")
        logger.info(f"    Passphrases: {summary['passphrases']['total']} "
                   f"({summary['passphrases']['created']} created)")

        task.status = TaskStatus.COMPLETED
        task.output_data = summary

    except Exception as e:
        logger.error(f"  ❌ Audit failed: {str(e)}")
        task.status = TaskStatus.FAILED
        task.error_message = str(e)

    return [task]
