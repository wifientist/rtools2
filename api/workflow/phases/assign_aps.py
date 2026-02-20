"""
V2 Phase: Assign APs to AP Group

Handles per-unit AP assignment:
1. Match APs by serial number or name from the venue AP list
2. Assign matched APs to the unit's AP Group

SSID activation is handled separately by the activate_network phase.

Receives `all_venue_aps` from the validate phase's global results
to avoid per-unit API calls for the venue AP list.
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Tuple

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


@register_phase("assign_aps", "Assign APs to AP Group")
class AssignAPsPhase(PhaseExecutor):
    """
    Find APs and assign them to the unit's AP Group.

    SSID activation is handled by the activate_network phase.
    This phase only handles AP matching and group assignment.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        ap_group_id: str
        ap_group_name: str = ""
        ssid_name: str = ""
        ap_serial_numbers: List[str] = Field(default_factory=list)
        # Pre-fetched from validate phase global results
        all_venue_aps: List[Dict[str, Any]] = Field(default_factory=list)

    class Outputs(BaseModel):
        aps_matched: int = 0
        aps_assigned: int = 0
        aps_already_in_group: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Assign APs to this unit's AP Group."""
        await self.emit(
            f"[{inputs.unit_number}] Assigning APs to "
            f"'{inputs.ap_group_name}'..."
        )

        # Step 1: Find matching APs
        matched_aps = self._find_matching_aps(
            inputs.ap_serial_numbers, inputs.all_venue_aps
        )

        aps_assigned = 0
        aps_already_in_group = 0

        # Step 2: Assign APs to AP Group
        if matched_aps:
            logger.info(
                f"[{inputs.unit_number}] Matched {len(matched_aps)} APs, "
                f"assigning to group..."
            )
            aps_assigned, aps_already_in_group = await self._assign_aps_to_group(
                inputs.unit_number,
                matched_aps,
                inputs.ap_group_id,
                inputs.ap_group_name,
            )
        elif inputs.ap_serial_numbers:
            logger.warning(
                f"[{inputs.unit_number}] No APs matched from "
                f"{len(inputs.ap_serial_numbers)} identifiers"
            )
            await self.emit(
                f"[{inputs.unit_number}] No APs matched from "
                f"{len(inputs.ap_serial_numbers)} identifiers",
                "warning",
            )
        else:
            logger.info(
                f"[{inputs.unit_number}] No APs specified - "
                f"skipping AP assignment"
            )

        # Summary
        if matched_aps:
            skip_info = ""
            if aps_already_in_group > 0:
                skip_info = f", {aps_already_in_group} already in group"
            message = (
                f"Assigned {aps_assigned} APs to "
                f"'{inputs.ap_group_name}'{skip_info}"
            )
        else:
            message = (
                f"No APs to assign to '{inputs.ap_group_name}'"
            )

        await self.emit(f"[{inputs.unit_number}] {message}", "success")

        return self.Outputs(
            aps_matched=len(matched_aps),
            aps_assigned=aps_assigned,
            aps_already_in_group=aps_already_in_group,
        )

    # =========================================================================
    # AP Matching
    # =========================================================================

    def _find_matching_aps(
        self,
        ap_identifiers: List[str],
        all_venue_aps: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Find APs matching the given identifiers (serial or name)."""
        if not ap_identifiers:
            return []

        matched = []
        for identifier in ap_identifiers:
            for ap in all_venue_aps:
                if (
                    ap.get('serialNumber') == identifier
                    or ap.get('name') == identifier
                    or identifier in ap.get('name', '')
                ):
                    matched.append(ap)
                    logger.debug(
                        f"Matched AP: {ap.get('name')} "
                        f"({ap.get('serialNumber')})"
                    )
                    break

        if len(matched) != len(ap_identifiers):
            logger.warning(
                f"Only matched {len(matched)}/{len(ap_identifiers)} APs"
            )

        return matched

    # =========================================================================
    # AP Assignment
    # =========================================================================

    async def _assign_aps_to_group(
        self,
        unit_number: str,
        matched_aps: List[Dict],
        ap_group_id: str,
        ap_group_name: str,
    ) -> Tuple[int, int]:
        """
        Assign APs to the AP Group.

        Returns:
            (assigned_count, already_in_group_count)
        """
        assigned = 0
        skipped = 0

        for ap in matched_aps:
            current_group_id = ap.get('apGroupId')
            ap_serial = ap.get('serialNumber')
            ap_name = ap.get('name', ap_serial)

            # Idempotency: skip if already in correct group
            if current_group_id == ap_group_id:
                skipped += 1
                logger.debug(
                    f"[{unit_number}] {ap_name} already in group "
                    f"'{ap_group_name}' - skipping"
                )
                continue

            try:
                # Use ActivityTracker for bulk polling if available
                use_activity_tracker = self.context.activity_tracker is not None

                result = await self.r1_client.venues.assign_ap_to_group(
                    tenant_id=self.tenant_id,
                    venue_id=self.venue_id,
                    ap_group_id=ap_group_id,
                    ap_serial_number=ap_serial,
                    wait_for_completion=not use_activity_tracker,
                )

                # If using tracker, wait via centralized polling
                if use_activity_tracker and result:
                    request_id = result.get('requestId') if isinstance(result, dict) else None
                    if request_id:
                        activity_result = await self.fire_and_wait(request_id)
                        if not activity_result.success:
                            raise RuntimeError(f"AP assignment failed: {activity_result.error}")

                assigned += 1
                logger.info(
                    f"[{unit_number}] Assigned {ap_name} to "
                    f"'{ap_group_name}' ({assigned}/{len(matched_aps) - skipped})"
                )
            except Exception as e:
                logger.warning(
                    f"[{unit_number}] Failed to assign AP {ap_serial}: {e}"
                )

        return assigned, skipped

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Estimate API calls for AP assignment."""
        ap_count = len(inputs.ap_serial_numbers)

        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=ap_count,
            notes=[
                f"{ap_count} APs to assign" if ap_count else "No APs to assign",
            ],
        )
