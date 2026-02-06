"""
V2 Phase: Audit Cloudpath Import Results

Summarizes the import by collecting results from all previous phases.
Provides statistics on created/reused/skipped resources.
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


class ResourceSummary(BaseModel):
    """Summary for a single resource type."""
    total: int = 0
    created: int = 0
    reused: int = 0
    skipped: int = 0
    failed: int = 0


@register_phase("cloudpath_audit", "Audit Import Results")
class CloudpathAuditPhase(PhaseExecutor):
    """
    Audit and summarize the cloudpath import results.

    Collects statistics from all previous phases and generates
    a comprehensive summary report.
    """

    class Inputs(BaseModel):
        import_mode: str = "property_wide"
        # From create_identity_groups phase
        identity_group_ids: Dict[str, str] = Field(
            default_factory=dict,
            description="Created identity groups"
        )
        identity_groups_reused: int = 0
        # From create_dpsk_pools phase
        dpsk_pool_ids: Dict[str, str] = Field(
            default_factory=dict,
            description="Created DPSK pools"
        )
        dpsk_pools_reused: bool = False
        # From create_passphrases phase
        created_count: int = 0
        failed_count: int = 0
        skipped_count: int = 0
        # From validation
        total_passphrases: Optional[int] = None
        unit_count: Optional[int] = None

    class Outputs(BaseModel):
        summary: Dict[str, ResourceSummary] = Field(default_factory=dict)
        import_mode: str = "property_wide"
        success: bool = True
        message: str = ""

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Generate audit summary."""
        await self.emit("Auditing import results...")

        # Brain automatically aggregates per-unit outputs for global phases
        # Inputs now contain summed counts from all units

        # Identity Groups summary
        ig_total = len(inputs.identity_group_ids)
        ig_summary = ResourceSummary(
            total=ig_total,
            created=ig_total - inputs.identity_groups_reused,
            reused=inputs.identity_groups_reused,
        )

        # DPSK Pools summary
        pool_total = len(inputs.dpsk_pool_ids)
        pool_summary = ResourceSummary(
            total=pool_total,
            created=pool_total if not inputs.dpsk_pools_reused else 0,
            reused=pool_total if inputs.dpsk_pools_reused else 0,
        )

        # Passphrases summary
        pp_total = inputs.total_passphrases or (
            inputs.created_count + inputs.failed_count + inputs.skipped_count
        )
        pp_summary = ResourceSummary(
            total=pp_total,
            created=inputs.created_count,
            skipped=inputs.skipped_count,
            failed=inputs.failed_count,
        )

        summary = {
            'identity_groups': ig_summary,
            'dpsk_pools': pool_summary,
            'passphrases': pp_summary,
        }

        # Log summary
        logger.info("Cloudpath Import Summary:")
        logger.info(f"  Mode: {inputs.import_mode}")
        if inputs.unit_count:
            logger.info(f"  Units: {inputs.unit_count}")
        logger.info(
            f"  Identity Groups: {ig_summary.total} "
            f"({ig_summary.created} created, {ig_summary.reused} reused)"
        )
        logger.info(
            f"  DPSK Pools: {pool_summary.total} "
            f"({pool_summary.created} created, {pool_summary.reused} reused)"
        )
        logger.info(
            f"  Passphrases: {pp_summary.total} "
            f"({pp_summary.created} created, {pp_summary.skipped} skipped, "
            f"{pp_summary.failed} failed)"
        )

        # Determine success
        success = pp_summary.failed == 0

        # Generate message
        if success:
            message = (
                f"Import complete: {pp_summary.created} passphrases created"
            )
            if pp_summary.skipped:
                message += f", {pp_summary.skipped} skipped"
            level = "success"
        else:
            message = (
                f"Import completed with errors: {pp_summary.created} created, "
                f"{pp_summary.failed} failed"
            )
            level = "warning"

        await self.emit(message, level)

        return self.Outputs(
            summary=summary,
            import_mode=inputs.import_mode,
            success=success,
            message=message,
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Audit phase just summarizes - always valid."""
        return PhaseValidation(
            valid=True,
            will_create=False,
            estimated_api_calls=0,
            notes=["Will generate import summary"],
        )
