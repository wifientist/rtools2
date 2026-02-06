"""
V2 Phase: Validate PSK Workflow (Phase 0)

Pre-validates the entire PSK workflow before execution:
1. Builds UnitMapping for every unit (plan names, input config)
2. Checks existing R1 resources (AP groups, networks)
3. Fetches venue APs for downstream AP assignment phase
4. Surfaces conflicts and builds dry-run summary
5. Returns validation result for user confirmation

The Brain handles this phase specially:
- Outputs.unit_mappings → populates job.units
- Outputs.validation_result → populates job.validation_result
- Remaining outputs → stored in global_phase_results for downstream phases
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.v2.models import (
    UnitMapping,
    UnitPlan,
    UnitResolved,
    ValidationResult,
    ValidationSummary,
    ConflictDetail,
    ResourceAction,
)

logger = logging.getLogger(__name__)


@register_phase("validate", "Validate & Plan")
class ValidatePSKPhase(PhaseExecutor):
    """
    Phase 0: Validate and plan the PSK workflow.

    Builds unit mappings, checks existing resources, and returns
    a dry-run summary for user confirmation.
    """

    class Inputs(BaseModel):
        units: List[Dict[str, Any]]
        ap_group_prefix: str = ""
        ap_group_postfix: str = ""
        name_conflict_resolution: str = "keep"
        configure_lan_ports: bool = False

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = Field(default_factory=dict)
        validation_result: Optional[ValidationResult] = None
        all_venue_aps: List[Dict[str, Any]] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Build unit mappings and validate resources."""
        await self.emit("Phase 0: Validating workflow plan...")

        units = inputs.units
        if not units:
            return self.Outputs(
                validation_result=ValidationResult(
                    valid=False,
                    summary=ValidationSummary(total_units=0),
                    conflicts=[ConflictDetail(
                        resource_type="units",
                        resource_name="input",
                        description="No units provided",
                        severity="error",
                    )],
                ),
            )

        # =====================================================================
        # 1. Build unit mappings with planned names
        # =====================================================================
        await self.emit(f"Building plan for {len(units)} units...")
        unit_mappings: Dict[str, UnitMapping] = {}

        for unit_data in units:
            unit_number = str(unit_data.get('unit_number', unit_data.get('id', '')))
            unit_id = f"unit_{unit_number}"

            # Compute planned names
            ap_group_name = (
                f"{inputs.ap_group_prefix}{unit_number}{inputs.ap_group_postfix}"
            )
            ssid_name = unit_data.get('ssid_name', '')
            network_name = unit_data.get('network_name') or ssid_name

            plan = UnitPlan(
                ap_group_name=ap_group_name,
                network_name=network_name,
                ap_serial_numbers=unit_data.get('ap_identifiers', []),
                lan_port_config=(
                    unit_data.get('lan_port_config')
                    if inputs.configure_lan_ports else None
                ),
            )

            mapping = UnitMapping(
                unit_id=unit_id,
                unit_number=unit_number,
                plan=plan,
                input_config={
                    'ssid_name': ssid_name,
                    'ssid_password': unit_data.get('ssid_password', ''),
                    'security_type': unit_data.get('security_type', 'WPA3'),
                    'default_vlan': str(unit_data.get('default_vlan', '1')),
                    'network_name': network_name,
                    'ap_group_name': ap_group_name,
                    'name_conflict_resolution': inputs.name_conflict_resolution,
                    'ap_serial_numbers': unit_data.get('ap_identifiers', []),
                },
            )

            unit_mappings[unit_id] = mapping

        # =====================================================================
        # 2. Check existing resources in R1
        # =====================================================================
        await self.emit("Checking existing resources...")

        summary = ValidationSummary(total_units=len(units))
        conflicts: List[ConflictDetail] = []
        actions: List[ResourceAction] = []

        for unit_id, mapping in unit_mappings.items():
            # Check AP Group
            try:
                existing_group = await self.r1_client.venues.find_ap_group_by_name(
                    self.tenant_id, self.venue_id, mapping.plan.ap_group_name
                )

                if existing_group and existing_group.get('name') == mapping.plan.ap_group_name:
                    mapping.plan.ap_group_exists = True
                    mapping.plan.will_create_ap_group = False
                    mapping.resolved.ap_group_id = existing_group.get('id')
                    summary.ap_groups_to_reuse += 1
                    actions.append(ResourceAction(
                        resource_type="ap_group",
                        name=mapping.plan.ap_group_name,
                        action="reuse",
                        existing_id=existing_group.get('id'),
                    ))
                else:
                    summary.ap_groups_to_create += 1
                    actions.append(ResourceAction(
                        resource_type="ap_group",
                        name=mapping.plan.ap_group_name,
                        action="create",
                    ))
            except Exception as e:
                logger.warning(
                    f"Error checking AP Group for {unit_id}: {e}"
                )
                summary.ap_groups_to_create += 1

            # Check WiFi Network
            ssid_name = mapping.input_config.get('ssid_name')
            network_name = mapping.plan.network_name

            try:
                existing_by_ssid = await self.r1_client.networks.find_wifi_network_by_ssid(
                    self.tenant_id, self.venue_id, ssid_name
                )

                if existing_by_ssid:
                    mapping.plan.network_exists = True
                    mapping.plan.will_create_network = False
                    mapping.resolved.network_id = existing_by_ssid.get('id')
                    summary.networks_to_reuse += 1
                    actions.append(ResourceAction(
                        resource_type="wifi_network",
                        name=ssid_name,
                        action="reuse",
                        existing_id=existing_by_ssid.get('id'),
                    ))
                else:
                    # Check for name conflict
                    existing_by_name = await self.r1_client.networks.find_wifi_network_by_name(
                        self.tenant_id, self.venue_id, network_name
                    )

                    if existing_by_name:
                        existing_ssid = existing_by_name.get('ssid', 'unknown')
                        conflicts.append(ConflictDetail(
                            unit_id=unit_id,
                            resource_type="wifi_network",
                            resource_name=network_name,
                            description=(
                                f"Network name '{network_name}' already in use "
                                f"by SSID '{existing_ssid}'"
                            ),
                            severity="error",
                        ))
                    else:
                        summary.networks_to_create += 1
                        actions.append(ResourceAction(
                            resource_type="wifi_network",
                            name=network_name,
                            action="create",
                        ))
            except Exception as e:
                logger.warning(
                    f"Error checking network for {unit_id}: {e}"
                )
                summary.networks_to_create += 1

        # =====================================================================
        # 3. Fetch venue APs for downstream assign_aps phase
        # =====================================================================
        await self.emit("Fetching venue APs...")
        all_venue_aps = []

        try:
            aps_response = await self.r1_client.venues.get_aps_by_tenant_venue(
                self.tenant_id, self.venue_id
            )
            all_venue_aps = aps_response.get('data', [])
            logger.info(f"Found {len(all_venue_aps)} APs in venue")
            await self.emit(f"Found {len(all_venue_aps)} APs in venue")
        except Exception as e:
            logger.warning(f"Failed to fetch venue APs: {e}")
            await self.emit(f"Warning: Could not fetch venue APs: {e}", "warning")

        # =====================================================================
        # 4. Estimate total API calls
        # =====================================================================
        estimated_api_calls = (
            summary.ap_groups_to_create  # AP Group creation
            + summary.networks_to_create  # Network creation
            + len(units)  # SSID activation (1 per unit)
            + len(units) * 3  # SSID→AP Group config (3 per unit)
        )

        # AP assignment calls
        total_aps = sum(
            len(m.plan.ap_serial_numbers) for m in unit_mappings.values()
        )
        estimated_api_calls += total_aps  # 1 per AP assignment

        summary.total_api_calls = estimated_api_calls

        # =====================================================================
        # 5. Build validation result
        # =====================================================================
        has_errors = any(c.severity == "error" for c in conflicts)

        validation_result = ValidationResult(
            valid=not has_errors,
            conflicts=conflicts,
            summary=summary,
            unit_plans={
                uid: mapping.plan for uid, mapping in unit_mappings.items()
            },
            actions=actions,
        )

        # Summary message
        summary_parts = []
        if summary.ap_groups_to_create > 0:
            summary_parts.append(f"{summary.ap_groups_to_create} AP groups to create")
        if summary.ap_groups_to_reuse > 0:
            summary_parts.append(f"{summary.ap_groups_to_reuse} AP groups to reuse")
        if summary.networks_to_create > 0:
            summary_parts.append(f"{summary.networks_to_create} networks to create")
        if summary.networks_to_reuse > 0:
            summary_parts.append(f"{summary.networks_to_reuse} networks to reuse")

        summary_str = ", ".join(summary_parts) if summary_parts else "no changes"
        level = "success" if validation_result.valid else "error"
        await self.emit(
            f"Validation complete: {summary_str} "
            f"(~{estimated_api_calls} API calls)",
            level,
        )

        if conflicts:
            for conflict in conflicts:
                await self.emit(
                    f"Conflict: {conflict.description}", "error"
                )

        return self.Outputs(
            unit_mappings=unit_mappings,
            validation_result=validation_result,
            all_venue_aps=all_venue_aps,
        )
