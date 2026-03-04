"""
V2 Phase: Validate LAN Port Configuration (Phase 0)

Validates AP port configuration by matching CSV AP identifiers against
venue APs, resolving models to port categories, and computing a preview
of what port changes will be applied.
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.v2.models import (
    UnitMapping,
    UnitPlan,
    ValidationResult,
    ValidationSummary,
    ConflictDetail,
    ResourceAction,
)
from r1api.models.ap_models import get_model_info

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PORT_CONFIGS = {
    'one_port_lan1_uplink': [
        {'mode': 'uplink'}, {'mode': 'ignore'},
    ],
    'one_port_lan2_uplink': [
        {'mode': 'ignore'}, {'mode': 'uplink'},
    ],
    'two_port': [
        {'mode': 'ignore'}, {'mode': 'ignore'}, {'mode': 'uplink'},
    ],
    'four_port': [
        {'mode': 'ignore'}, {'mode': 'ignore'}, {'mode': 'ignore'},
        {'mode': 'ignore'}, {'mode': 'uplink'},
    ],
}


@register_phase(
    "validate_lan_ports", "Validate AP Port Config"
)
class ValidateLANPortsPhase(PhaseExecutor):
    """
    Phase 0: Validate and plan standalone LAN port configuration.

    Matches CSV AP identifiers to venue APs, resolves models,
    computes per-AP port changes, and returns a rich plan for
    user review before execution.
    """

    class Inputs(BaseModel):
        units: List[Dict[str, Any]]
        model_port_configs: Optional[Dict[str, List]] = None

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = Field(
            default_factory=dict
        )
        validation_result: Optional[ValidationResult] = None
        all_venue_aps: List[Dict[str, Any]] = Field(
            default_factory=list
        )
        ap_match_summary: Dict[str, Any] = Field(
            default_factory=dict
        )

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Build unit mappings, fetch venue APs, and compute port change preview."""
        await self.emit("Validating LAN port configuration...")

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

        # Build unit mappings
        unit_mappings: Dict[str, UnitMapping] = {}

        for unit_data in units:
            unit_number = str(
                unit_data.get('unit_number', unit_data.get('id', ''))
            )
            unit_id = f"unit_{unit_number}"

            plan = UnitPlan(
                ap_serial_numbers=unit_data.get(
                    'ap_identifiers', []
                ),
                lan_port_config=unit_data.get('lan_port_config'),
            )

            mapping = UnitMapping(
                unit_id=unit_id,
                unit_number=unit_number,
                plan=plan,
                input_config={
                    'default_vlan': str(
                        unit_data.get('default_vlan', '1')
                    ),
                    'ap_serial_numbers': unit_data.get(
                        'ap_identifiers', []
                    ),
                },
            )
            unit_mappings[unit_id] = mapping

        # Fetch venue APs
        await self.emit("Fetching venue APs...")
        all_venue_aps = []

        try:
            aps_response = (
                await self.r1_client.venues
                .get_aps_by_tenant_venue(
                    self.tenant_id, self.venue_id
                )
            )
            all_venue_aps = aps_response.get('data', [])
            await self.emit(
                f"Found {len(all_venue_aps)} APs in venue"
            )
        except Exception as e:
            logger.warning(f"Failed to fetch venue APs: {e}")
            await self.emit(
                f"Warning: Could not fetch venue APs: {e}",
                "warning",
            )

        # --- AP matching and port change preview ---
        await self.emit("Matching APs and computing port changes...")

        mpc = inputs.model_port_configs or DEFAULT_MODEL_PORT_CONFIGS

        # Build venue AP lookups (same pattern as ConfigureLANPortsPhase)
        ap_lookup_by_serial = {
            ap.get('serialNumber', '').upper(): ap
            for ap in all_venue_aps
            if ap.get('serialNumber')
        }
        ap_lookup_by_name = {
            ap.get('name', '').lower(): ap
            for ap in all_venue_aps
            if ap.get('name')
        }

        total_csv_aps = sum(
            len(m.plan.ap_serial_numbers)
            for m in unit_mappings.values()
        )
        matched_aps = 0
        not_found_aps: List[str] = []
        skipped_zero_port = 0
        skipped_no_config = 0
        model_breakdown: Dict[str, int] = {}
        actions: List[ResourceAction] = []
        conflicts: List[ConflictDetail] = []
        estimated_api_calls = 0

        for unit_id, mapping in unit_mappings.items():
            default_vlan = mapping.input_config.get(
                'default_vlan', '1'
            )

            for identifier in mapping.plan.ap_serial_numbers:
                ap = (
                    ap_lookup_by_serial.get(identifier.upper())
                    or ap_lookup_by_name.get(identifier.lower())
                )

                if not ap:
                    not_found_aps.append(identifier)
                    continue

                matched_aps += 1
                model = ap.get('model', '')
                info = get_model_info(model)

                # Determine model category and config key
                if not info['has_lan_ports'] or info['port_count'] == 0:
                    category = '0-port'
                    skipped_zero_port += 1
                    model_breakdown[category] = (
                        model_breakdown.get(category, 0) + 1
                    )
                    continue

                category, config_key = self._resolve_category(info)
                model_breakdown[category] = (
                    model_breakdown.get(category, 0) + 1
                )

                # Get port configs for this model category
                port_configs_list = mpc.get(config_key, [])
                if not port_configs_list:
                    skipped_no_config += 1
                    continue

                # Compute port changes for this AP
                ap_name = ap.get('name') or identifier
                port_changes = []

                for idx, pc in enumerate(port_configs_list):
                    port_id = f"LAN{idx + 1}"
                    mode = pc.get('mode', 'ignore')
                    if mode == 'match':
                        port_changes.append(
                            f"{port_id} \u2192 VLAN {default_vlan}"
                        )
                    elif mode == 'specific':
                        port_changes.append(
                            f"{port_id} \u2192 VLAN {pc.get('vlan', 1)}"
                        )
                    elif mode == 'disable':
                        port_changes.append(
                            f"{port_id} \u2192 disabled"
                        )

                if port_changes:
                    # ~3 API calls per AP (disable inheritance,
                    # activate ACCESS profile, set VLAN override)
                    estimated_api_calls += 3
                    actions.append(ResourceAction(
                        resource_type="ap_port_config",
                        name=ap_name,
                        action="configure",
                        notes=[
                            f"{model}: {', '.join(port_changes)}"
                        ],
                    ))
                else:
                    skipped_no_config += 1

        # Build warnings for not-found APs
        if not_found_aps:
            desc = (
                f"{len(not_found_aps)} AP(s) not found in venue: "
                + ", ".join(not_found_aps[:10])
            )
            if len(not_found_aps) > 10:
                desc += f" ... and {len(not_found_aps) - 10} more"
            conflicts.append(ConflictDetail(
                resource_type="ap",
                resource_name="not_found",
                description=desc,
                severity="warning",
            ))

        if skipped_zero_port > 0:
            conflicts.append(ConflictDetail(
                resource_type="ap",
                resource_name="zero_port_models",
                description=(
                    f"{skipped_zero_port} AP(s) have no configurable "
                    f"LAN ports (0-port models) and will be skipped"
                ),
                severity="info",
            ))

        will_configure = len(actions)
        validation_result = ValidationResult(
            valid=True,
            summary=ValidationSummary(
                total_units=len(unit_mappings),
                total_api_calls=estimated_api_calls,
            ),
            conflicts=conflicts,
            actions=actions,
        )

        ap_match_summary = {
            "total_csv_aps": total_csv_aps,
            "matched": matched_aps,
            "not_found": len(not_found_aps),
            "not_found_identifiers": not_found_aps[:50],
            "will_configure": will_configure,
            "skipped_zero_port": skipped_zero_port,
            "skipped_no_config": skipped_no_config,
            "model_breakdown": model_breakdown,
            "estimated_api_calls": estimated_api_calls,
        }

        await self.emit(
            f"Matched {matched_aps}/{total_csv_aps} APs, "
            f"{will_configure} will be configured, "
            f"{len(not_found_aps)} not found",
            "success",
        )

        return self.Outputs(
            unit_mappings=unit_mappings,
            validation_result=validation_result,
            all_venue_aps=all_venue_aps,
            ap_match_summary=ap_match_summary,
        )

    @staticmethod
    def _resolve_category(
        info: Dict[str, Any],
    ) -> tuple:
        """Map model info to a display category and config key."""
        port_count = info['port_count']
        uplink = info.get('uplink_port', '')

        if port_count == 1:
            if uplink == 'LAN1':
                return '1-port (LAN1 uplink)', 'one_port_lan1_uplink'
            return '1-port (LAN2 uplink)', 'one_port_lan2_uplink'
        elif port_count == 2:
            return '2-port', 'two_port'
        elif port_count == 4:
            return '4-port', 'four_port'
        return 'unknown', ''
