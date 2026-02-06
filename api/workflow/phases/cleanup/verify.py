"""
V2 Phase: Verify Cleanup

Summarizes cleanup results from all delete phases.
Always runs last to provide a final status report.
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)


class DeletePhaseResult(BaseModel):
    """Result from a single delete phase."""
    deleted_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    errors: List[str] = Field(default_factory=list)


@register_phase("verify_cleanup", "Verify Cleanup")
class VerifyCleanupPhase(PhaseExecutor):
    """
    Verify cleanup completion and emit summary.

    Collects results from all delete phases and reports totals.
    """

    class Inputs(BaseModel):
        delete_passphrases_result: DeletePhaseResult = Field(
            default_factory=DeletePhaseResult
        )
        delete_dpsk_pools_result: DeletePhaseResult = Field(
            default_factory=DeletePhaseResult
        )
        delete_identities_result: DeletePhaseResult = Field(
            default_factory=DeletePhaseResult
        )
        delete_identity_groups_result: DeletePhaseResult = Field(
            default_factory=DeletePhaseResult
        )
        delete_networks_result: DeletePhaseResult = Field(
            default_factory=DeletePhaseResult
        )
        delete_ap_groups_result: DeletePhaseResult = Field(
            default_factory=DeletePhaseResult
        )

    class Outputs(BaseModel):
        total_deleted: int = 0
        total_failed: int = 0
        total_skipped: int = 0
        summary: Dict[str, Dict[str, int]] = Field(
            default_factory=dict
        )

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Summarize cleanup results."""
        await self.emit("Verifying cleanup results...")

        results = {
            'passphrases': inputs.delete_passphrases_result,
            'dpsk_pools': inputs.delete_dpsk_pools_result,
            'identities': inputs.delete_identities_result,
            'identity_groups': inputs.delete_identity_groups_result,
            'wifi_networks': inputs.delete_networks_result,
            'ap_groups': inputs.delete_ap_groups_result,
        }

        total_deleted = 0
        total_failed = 0
        total_skipped = 0
        summary = {}

        for resource_type, result in results.items():
            summary[resource_type] = {
                'deleted': result.deleted_count,
                'failed': result.failed_count,
                'skipped': result.skipped_count,
            }
            total_deleted += result.deleted_count
            total_failed += result.failed_count
            total_skipped += result.skipped_count

            if result.deleted_count or result.failed_count:
                logger.info(
                    f"  {resource_type}: "
                    f"{result.deleted_count} deleted, "
                    f"{result.failed_count} failed"
                )

        # Summary message
        parts = [f"{total_deleted} deleted"]
        if total_failed:
            parts.append(f"{total_failed} failed")
        if total_skipped:
            parts.append(f"{total_skipped} skipped")

        level = "success" if total_failed == 0 else "warning"
        await self.emit(
            f"Cleanup complete: {', '.join(parts)}", level
        )

        return self.Outputs(
            total_deleted=total_deleted,
            total_failed=total_failed,
            total_skipped=total_skipped,
            summary=summary,
        )


# =============================================================================
# Legacy Adapter for cloudpath_router compatibility
# =============================================================================

async def execute(context: Dict[str, Any]) -> List:
    """Legacy adapter for cloudpath workflow execution."""
    from workflow.v2.models import Task, TaskStatus

    # Gather results from previous delete phases
    prev_results = context.get('previous_phase_results', {})

    total_deleted = 0
    total_failed = 0
    summary = {}

    for phase_id, phase_data in prev_results.items():
        if phase_id.startswith('delete_'):
            aggregated = phase_data.get('aggregated', {})
            deleted = sum(aggregated.get('deleted', [0]))
            failed = sum(aggregated.get('failed', [0]))
            total_deleted += deleted
            total_failed += failed
            summary[phase_id] = {'deleted': deleted, 'failed': failed}

    logger.info(f"Cleanup complete: {total_deleted} deleted, {total_failed} failed")

    return [Task(
        id="verify_cleanup",
        name=f"Cleanup: {total_deleted} deleted, {total_failed} failed",
        task_type="verify",
        status=TaskStatus.COMPLETED,
        output_data={
            'total_deleted': total_deleted,
            'total_failed': total_failed,
            'summary': summary
        }
    )]
