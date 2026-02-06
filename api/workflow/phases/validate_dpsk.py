"""
V2 Phase: Validate DPSK Workflow (Phase 0)

Pre-validates the entire DPSK workflow before execution:
1. Builds UnitMapping for every unit (plan names, input config)
2. Groups passphrases by unit (multiple CSV rows per unit_number)
3. Checks existing R1 resources (AP groups, identity groups, DPSK pools, networks)
4. Fetches venue APs for downstream AP assignment phase
5. Surfaces conflicts and builds dry-run summary
6. Returns validation result for user confirmation

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


@register_phase("validate_dpsk", "Validate & Plan (DPSK)")
class ValidateDPSKPhase(PhaseExecutor):
    """
    Phase 0: Validate and plan the DPSK workflow.

    Builds unit mappings (grouping passphrases per unit),
    checks existing resources, and returns a dry-run summary
    for user confirmation.
    """

    class Inputs(BaseModel):
        units: List[Dict[str, Any]]
        ap_group_prefix: str = ""
        ap_group_postfix: str = ""
        # Single shared resources for all units (no more per-unit pools)
        identity_group_name: str = ""
        dpsk_pool_name: str = ""
        dpsk_pool_settings: Dict[str, Any] = Field(default_factory=dict)
        name_conflict_resolution: str = "keep"
        configure_lan_ports: bool = False

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = Field(default_factory=dict)
        validation_result: Optional[ValidationResult] = None
        all_venue_aps: List[Dict[str, Any]] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Build unit mappings and validate DPSK resources."""
        await self.emit("Phase 0: Validating DPSK workflow plan...")

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
        # 1. Group passphrases by unit and build unit mappings
        # =====================================================================
        # In DPSK mode, multiple CSV rows can share the same unit_number.
        # Each row is a passphrase for that unit.
        await self.emit(f"Building plan for {len(units)} unit rows...")

        # Group rows by unit_number
        unit_groups: Dict[str, List[Dict[str, Any]]] = {}
        for unit_data in units:
            unit_number = str(
                unit_data.get('unit_number', unit_data.get('id', ''))
            )
            if unit_number not in unit_groups:
                unit_groups[unit_number] = []
            unit_groups[unit_number].append(unit_data)

        await self.emit(
            f"Found {len(unit_groups)} unique units "
            f"({len(units)} total rows)"
        )

        # Single shared identity group and DPSK pool for all units
        shared_identity_group_name = inputs.identity_group_name
        shared_dpsk_pool_name = inputs.dpsk_pool_name

        # Build one UnitMapping per unique unit_number
        unit_mappings: Dict[str, UnitMapping] = {}
        conflicts: List[ConflictDetail] = []

        # Track which unit will create the shared resources (first one)
        first_unit = True

        for unit_number, rows in unit_groups.items():
            unit_id = f"unit_{unit_number}"
            first_row = rows[0]  # Use first row for common fields

            # Compute planned names - AP group is per-unit
            ap_group_name = (
                f"{inputs.ap_group_prefix}{unit_number}"
                f"{inputs.ap_group_postfix}"
            )
            # Use shared names for identity group and DPSK pool
            identity_group_name = shared_identity_group_name
            dpsk_pool_name = shared_dpsk_pool_name

            ssid_name = first_row.get('ssid_name', '').strip()
            network_name = (first_row.get('network_name') or ssid_name).strip()

            # Validate SSID - must not contain tabs or control characters
            if '\t' in ssid_name or '\n' in ssid_name or '\r' in ssid_name:
                conflicts.append(ConflictDetail(
                    unit_id=f"unit_{unit_number}",
                    resource_type="ssid",
                    resource_name=ssid_name[:30],
                    description=(
                        f"Invalid SSID for unit {unit_number}: contains tab/newline. "
                        f"Check CSV column mapping. Raw value: {repr(ssid_name)}"
                    ),
                    severity="error",
                ))

            # Collect passphrases from all rows for this unit
            # Empty passphrases are allowed - user may import actual passwords
            # later via Cloudpath Import
            passphrases = []
            for row in rows:
                passphrase_value = (
                    row.get('ssid_password') or row.get('passphrase', '')
                )
                username = row.get('username', '')
                # Include entry if it has passphrase OR username OR other data
                # (allows for initially empty passphrases that will be imported later)
                if passphrase_value or username or row.get('email'):
                    passphrases.append({
                        'passphrase': passphrase_value,
                        'username': username,
                        'email': row.get('email'),
                        'description': row.get('description'),
                        'vlan_id': row.get('vlan_id'),
                    })

            # Only the first unit creates shared identity group and DPSK pool
            plan = UnitPlan(
                ap_group_name=ap_group_name,
                identity_group_name=identity_group_name,
                dpsk_pool_name=dpsk_pool_name,
                network_name=network_name,
                will_create_identity_group=first_unit,  # Only first unit creates
                will_create_dpsk_pool=first_unit,  # Only first unit creates
                passphrase_count=len(passphrases),
                ap_serial_numbers=first_row.get('ap_identifiers', []),
                lan_port_config=(
                    first_row.get('lan_port_config')
                    if inputs.configure_lan_ports else None
                ),
            )

            # After first unit, subsequent units just reference shared resources
            first_unit = False

            # Validate VLAN
            raw_vlan = str(first_row.get('default_vlan', '1')).strip()
            try:
                vlan_int = int(raw_vlan)
                if not 1 <= vlan_int <= 4094:
                    raise ValueError("out of range")
            except ValueError:
                conflicts.append(ConflictDetail(
                    unit_id=f"unit_{unit_number}",
                    resource_type="config",
                    resource_name="default_vlan",
                    description=(
                        f"Invalid VLAN for unit {unit_number}: '{raw_vlan}'. "
                        f"Expected number 1-4094. Check CSV column mapping."
                    ),
                    severity="error",
                ))
                raw_vlan = "1"  # Default to avoid downstream errors

            mapping = UnitMapping(
                unit_id=unit_id,
                unit_number=unit_number,
                plan=plan,
                input_config={
                    'ssid_name': ssid_name,
                    'network_name': network_name,
                    'default_vlan': raw_vlan,
                    'ap_group_name': ap_group_name,
                    'identity_group_name': identity_group_name,
                    'dpsk_pool_name': dpsk_pool_name,
                    'name_conflict_resolution': (
                        inputs.name_conflict_resolution
                    ),
                    'ap_serial_numbers': first_row.get(
                        'ap_identifiers', []
                    ),
                    'passphrases': passphrases,
                    'dpsk_pool_settings': inputs.dpsk_pool_settings,
                },
            )

            unit_mappings[unit_id] = mapping

        # =====================================================================
        # 2. Check existing resources in R1
        # =====================================================================
        await self.emit("Checking existing resources...")

        summary = ValidationSummary(total_units=len(unit_groups))
        actions: List[ResourceAction] = []

        # --- Check shared Identity Group ONCE (before loop) ---
        shared_ig_exists = False
        shared_ig_id: Optional[str] = None
        if shared_identity_group_name:
            try:
                existing_igs = (
                    await self.r1_client.identity.query_identity_groups(
                        tenant_id=self.tenant_id
                    )
                )
                ig_items = existing_igs.get(
                    'content', existing_igs.get('data', [])
                )
                ig_match = next(
                    (ig for ig in ig_items
                     if ig.get('name') == shared_identity_group_name),
                    None
                )
                if ig_match:
                    shared_ig_exists = True
                    shared_ig_id = ig_match.get('id')
                    summary.identity_groups_to_reuse = 1
                    actions.append(ResourceAction(
                        resource_type="identity_group",
                        name=shared_identity_group_name,
                        action="reuse",
                        existing_id=shared_ig_id,
                    ))
                else:
                    summary.identity_groups_to_create = 1
                    actions.append(ResourceAction(
                        resource_type="identity_group",
                        name=shared_identity_group_name,
                        action="create",
                    ))
            except Exception as e:
                logger.warning(
                    f"Error checking shared identity group: {e}"
                )
                summary.identity_groups_to_create = 1

        # --- Check shared DPSK Pool ONCE (before loop) ---
        shared_pool_exists = False
        shared_pool_id: Optional[str] = None
        if shared_dpsk_pool_name:
            try:
                existing_pools = (
                    await self.r1_client.dpsk.query_dpsk_pools(
                        tenant_id=self.tenant_id
                    )
                )
                pool_items = (
                    existing_pools
                    if isinstance(existing_pools, list)
                    else existing_pools.get(
                        'content', existing_pools.get('data', [])
                    )
                )
                pool_match = next(
                    (p for p in pool_items
                     if p.get('name') == shared_dpsk_pool_name),
                    None
                )
                if pool_match:
                    shared_pool_exists = True
                    shared_pool_id = pool_match.get('id')
                    summary.dpsk_pools_to_reuse = 1
                    actions.append(ResourceAction(
                        resource_type="dpsk_pool",
                        name=shared_dpsk_pool_name,
                        action="reuse",
                        existing_id=shared_pool_id,
                    ))
                else:
                    summary.dpsk_pools_to_create = 1
                    actions.append(ResourceAction(
                        resource_type="dpsk_pool",
                        name=shared_dpsk_pool_name,
                        action="create",
                    ))
            except Exception as e:
                logger.warning(
                    f"Error checking shared DPSK pool: {e}"
                )
                summary.dpsk_pools_to_create = 1

        # --- Check per-unit resources ---
        for unit_id, mapping in unit_mappings.items():
            # Update all units with shared resource status
            if shared_ig_exists:
                mapping.plan.identity_group_exists = True
                mapping.plan.will_create_identity_group = False
                mapping.resolved.identity_group_id = shared_ig_id
            if shared_pool_exists:
                mapping.plan.dpsk_pool_exists = True
                mapping.plan.will_create_dpsk_pool = False
                mapping.resolved.dpsk_pool_id = shared_pool_id

            # --- Check AP Group (per-unit) ---
            try:
                existing_group = (
                    await self.r1_client.venues.find_ap_group_by_name(
                        self.tenant_id,
                        self.venue_id,
                        mapping.plan.ap_group_name,
                    )
                )

                if (
                    existing_group
                    and existing_group.get('name')
                    == mapping.plan.ap_group_name
                ):
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

            # --- Check WiFi Network (per-unit) ---
            ssid_name = mapping.input_config.get('ssid_name')
            network_name = mapping.plan.network_name

            try:
                existing_by_ssid = (
                    await self.r1_client.networks
                    .find_wifi_network_by_ssid(
                        self.tenant_id, self.venue_id, ssid_name
                    )
                )

                if existing_by_ssid:
                    mapping.plan.network_exists = True
                    mapping.plan.will_create_network = False
                    mapping.resolved.network_id = (
                        existing_by_ssid.get('id')
                    )
                    summary.networks_to_reuse += 1
                    actions.append(ResourceAction(
                        resource_type="wifi_network",
                        name=ssid_name,
                        action="reuse",
                        existing_id=existing_by_ssid.get('id'),
                    ))
                else:
                    # Check for name conflict
                    existing_by_name = (
                        await self.r1_client.networks
                        .find_wifi_network_by_name(
                            self.tenant_id, self.venue_id, network_name
                        )
                    )

                    if existing_by_name:
                        existing_ssid = existing_by_name.get(
                            'ssid', 'unknown'
                        )
                        conflicts.append(ConflictDetail(
                            unit_id=unit_id,
                            resource_type="wifi_network",
                            resource_name=network_name,
                            description=(
                                f"Network name '{network_name}' already "
                                f"in use by SSID '{existing_ssid}'"
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

            # --- Count passphrases ---
            pp_count = mapping.plan.passphrase_count
            summary.passphrases_to_create += pp_count

        # =====================================================================
        # 3. Fetch venue APs for downstream assign_aps phase
        # =====================================================================
        await self.emit("Fetching venue APs...")
        all_venue_aps = []

        try:
            aps_response = (
                await self.r1_client.venues.get_aps_by_tenant_venue(
                    self.tenant_id, self.venue_id
                )
            )
            all_venue_aps = aps_response.get('data', [])
            logger.info(f"Found {len(all_venue_aps)} APs in venue")
            await self.emit(f"Found {len(all_venue_aps)} APs in venue")
        except Exception as e:
            logger.warning(f"Failed to fetch venue APs: {e}")
            await self.emit(
                f"Warning: Could not fetch venue APs: {e}", "warning"
            )

        # =====================================================================
        # 4. Estimate total API calls
        # =====================================================================
        estimated_api_calls = (
            summary.ap_groups_to_create
            + summary.identity_groups_to_create
            + summary.dpsk_pools_to_create
            + summary.networks_to_create * 2  # create + link DPSK service
            + summary.passphrases_to_create
            + len(unit_groups)  # SSID activation (1 per unit)
            + len(unit_groups) * 3  # SSID→AP Group config (3 per unit)
        )

        # AP assignment calls
        total_aps = sum(
            len(m.plan.ap_serial_numbers)
            for m in unit_mappings.values()
        )
        estimated_api_calls += total_aps

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
                uid: mapping.plan
                for uid, mapping in unit_mappings.items()
            },
            actions=actions,
        )

        # Summary message
        summary_parts = []
        if summary.ap_groups_to_create > 0:
            summary_parts.append(
                f"{summary.ap_groups_to_create} AP groups to create"
            )
        if summary.ap_groups_to_reuse > 0:
            summary_parts.append(
                f"{summary.ap_groups_to_reuse} AP groups to reuse"
            )
        if summary.identity_groups_to_create > 0:
            summary_parts.append(
                f"{summary.identity_groups_to_create} identity groups "
                f"to create"
            )
        if summary.identity_groups_to_reuse > 0:
            summary_parts.append(
                f"{summary.identity_groups_to_reuse} identity groups "
                f"to reuse"
            )
        if summary.dpsk_pools_to_create > 0:
            summary_parts.append(
                f"{summary.dpsk_pools_to_create} DPSK pools to create"
            )
        if summary.dpsk_pools_to_reuse > 0:
            summary_parts.append(
                f"{summary.dpsk_pools_to_reuse} DPSK pools to reuse"
            )
        if summary.networks_to_create > 0:
            summary_parts.append(
                f"{summary.networks_to_create} networks to create"
            )
        if summary.networks_to_reuse > 0:
            summary_parts.append(
                f"{summary.networks_to_reuse} networks to reuse"
            )
        if summary.passphrases_to_create > 0:
            summary_parts.append(
                f"{summary.passphrases_to_create} passphrases to create"
            )

        summary_str = (
            ", ".join(summary_parts) if summary_parts else "no changes"
        )
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
