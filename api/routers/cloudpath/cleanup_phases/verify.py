"""
Verify Cleanup Phase

Verifies that cleanup was successful by summarizing what was deleted.
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Verify cleanup completion and summarize results

    Args:
        context: Workflow context containing all phase results

    Returns:
        List containing single task with verification summary
    """
    logger.info("Phase 6: Verify Cleanup Complete")

    # Get results from all previous phases
    prev_results = context.get('previous_phase_results', {})

    # Extract task outputs from each phase
    passphrase_results = prev_results.get('delete_passphrases', {})
    pool_results = prev_results.get('delete_dpsk_pools', {})
    identity_results = prev_results.get('delete_identities', {})
    group_results = prev_results.get('delete_identity_groups', {})

    # Count tasks by status for each resource type
    summary = {
        'passphrases': {
            'deleted': 0,
            'failed': 0,
            'skipped': 0
        },
        'dpsk_pools': {
            'deleted': 0,
            'failed': 0,
            'skipped': 0
        },
        'identities': {
            'deleted': 0,
            'failed': 0,
            'skipped': 0
        },
        'identity_groups': {
            'deleted': 0,
            'failed': 0,
            'skipped': 0
        }
    }

    # Count passphrase deletions
    summary['passphrases']['deleted'] = passphrase_results.get('completed_count', 0)
    summary['passphrases']['failed'] = passphrase_results.get('failed_count', 0)

    # Count DPSK pool deletions
    summary['dpsk_pools']['deleted'] = pool_results.get('completed_count', 0)
    summary['dpsk_pools']['failed'] = pool_results.get('failed_count', 0)

    # Count identity deletions
    summary['identities']['deleted'] = identity_results.get('completed_count', 0)
    summary['identities']['failed'] = identity_results.get('failed_count', 0)

    # Count identity group deletions
    summary['identity_groups']['deleted'] = group_results.get('completed_count', 0)
    summary['identity_groups']['failed'] = group_results.get('failed_count', 0)

    logger.info("=" * 50)
    logger.info("CLEANUP SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Identities:       {summary['identities']['deleted']} deleted, "
               f"{summary['identities']['failed']} failed "
               f"(cascade-deleted passphrases)")
    logger.info(f"Passphrases:      {summary['passphrases']['deleted']} deleted, "
               f"{summary['passphrases']['failed']} failed "
               f"(safety net)")
    logger.info(f"DPSK Pools:       {summary['dpsk_pools']['deleted']} deleted, "
               f"{summary['dpsk_pools']['failed']} failed")
    logger.info(f"Identity Groups:  {summary['identity_groups']['deleted']} deleted, "
               f"{summary['identity_groups']['failed']} failed")
    logger.info("=" * 50)

    # Determine overall status
    total_deleted = (summary['identities']['deleted'] +
                    summary['passphrases']['deleted'] +
                    summary['dpsk_pools']['deleted'] +
                    summary['identity_groups']['deleted'])
    total_failed = (summary['identities']['failed'] +
                   summary['passphrases']['failed'] +
                   summary['dpsk_pools']['failed'] +
                   summary['identity_groups']['failed'])

    if total_failed > 0:
        status_msg = f"Cleanup completed: {total_deleted} deleted, {total_failed} failed"
        overall_status = TaskStatus.COMPLETED  # Still mark as completed since partial success is OK
    else:
        status_msg = f"Cleanup completed successfully: {total_deleted} resources deleted"
        overall_status = TaskStatus.COMPLETED

    task = Task(
        id="verify_cleanup",
        name="Verify cleanup completion",
        task_type="verify",
        status=overall_status,
        output_data={'summary': summary, 'message': status_msg}
    )

    return [task]
