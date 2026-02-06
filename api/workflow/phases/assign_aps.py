"""
V2 Phase: Assign APs to AP Group & Configure SSID

Handles the complex per-unit AP assignment:
1. Match APs by serial number or name from the venue AP list
2. Assign matched APs to the unit's AP Group
3. Configure SSID for the specific AP Group via 3-step R1 API process:
   a. Set isAllApGroups=false on the network
   b. Add AP Group to venue AP Groups list
   c. Configure SSID settings for the AP Group

Receives `all_venue_aps` from the validate phase's global results
to avoid per-unit API calls for the venue AP list.
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Tuple

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


@register_phase("assign_aps", "Assign APs & Configure SSID")
class AssignAPsPhase(PhaseExecutor):
    """
    Find APs, assign to AP Group, and configure SSID for the AP Group.

    This is the most complex per-unit phase. It:
    - Uses pre-fetched venue AP data (from validate phase global results)
    - Matches APs by serial number or name
    - Assigns APs to the correct AP Group
    - Runs the 3-step R1 API process to configure SSID on the AP Group
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_id: str
        ap_group_id: str
        ap_group_name: str = ""
        ssid_name: str = ""
        default_vlan: str = "1"
        ap_serial_numbers: List[str] = Field(default_factory=list)
        # Pre-fetched from validate phase global results
        all_venue_aps: List[Dict[str, Any]] = Field(default_factory=list)

    class Outputs(BaseModel):
        aps_matched: int = 0
        aps_assigned: int = 0
        aps_already_in_group: int = 0
        ssid_configured: bool = False
        ssid_already_configured: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Assign APs and configure SSID for this unit's AP Group."""
        await self.emit(
            f"[{inputs.unit_number}] Processing unit "
            f"'{inputs.ssid_name or inputs.network_id}'..."
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

        # Step 3: Configure SSID for the AP Group (3-step R1 process)
        ssid_configured, ssid_already = await self._configure_ssid_for_ap_group(
            inputs.unit_number,
            inputs.ssid_name,
            inputs.network_id,
            inputs.ap_group_id,
            inputs.ap_group_name,
            inputs.default_vlan,
        )

        # Summary
        if matched_aps:
            skip_info = ""
            if aps_already_in_group > 0:
                skip_info = f", {aps_already_in_group} already in group"
            message = (
                f"Configured {len(matched_aps)} APs with SSID "
                f"'{inputs.ssid_name}' (VLAN {inputs.default_vlan}){skip_info}"
            )
        else:
            message = (
                f"Activated SSID '{inputs.ssid_name}' (VLAN {inputs.default_vlan}) "
                f"on AP Group '{inputs.ap_group_name}'"
            )

        await self.emit(f"[{inputs.unit_number}] {message}", "success")

        return self.Outputs(
            aps_matched=len(matched_aps),
            aps_assigned=aps_assigned,
            aps_already_in_group=aps_already_in_group,
            ssid_configured=ssid_configured,
            ssid_already_configured=ssid_already,
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

    # =========================================================================
    # SSID Configuration (3-Step R1 Process)
    # =========================================================================

    async def _configure_ssid_for_ap_group(
        self,
        unit_number: str,
        ssid_name: str,
        network_id: str,
        ap_group_id: str,
        ap_group_name: str,
        default_vlan: str,
    ) -> Tuple[bool, bool]:
        """
        Configure SSID for a specific AP Group using the 3-step R1 API process.

        Returns:
            (configured, already_configured)
        """
        await self.emit(
            f"[{unit_number}] Configuring SSID for AP Group..."
        )

        try:
            debug_delay = self.context.options.get('debug_delay', 0)

            await self.r1_client.venues.configure_ssid_for_specific_ap_group(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                wifi_network_id=network_id,
                ap_group_id=ap_group_id,
                radio_types=["2.4-GHz", "5-GHz", "6-GHz"],
                vlan_id=int(default_vlan) if default_vlan else None,
                wait_for_completion=True,
                debug_delay=debug_delay,
            )

            logger.info(
                f"[{unit_number}] 3-step SSID config complete for "
                f"AP Group '{ap_group_name}'"
            )
            await self.emit(
                f"[{unit_number}] SSID configured for AP Group", "success"
            )
            return True, False

        except Exception as e:
            raise RuntimeError(
                f"Configuring SSID for AP Group failed: {e}"
            )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Estimate API calls for AP assignment."""
        ap_count = len(inputs.ap_serial_numbers)
        # 3 API calls for SSID config + 1 per AP assignment
        estimated = 3 + ap_count

        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=estimated,
            notes=[
                f"{ap_count} APs to assign" if ap_count else "No APs to assign",
                "3-step SSID→AP Group configuration",
            ],
        )
