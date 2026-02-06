"""
V2 Phase: Validate LAN Port Configuration (Phase 0)

Lightweight validation for the standalone AP LAN Port Config workflow.
Builds unit mappings and fetches venue APs.
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
)

logger = logging.getLogger(__name__)


@register_phase(
    "validate_lan_ports", "Validate AP Port Config"
)
class ValidateLANPortsPhase(PhaseExecutor):
    """
    Phase 0: Validate and plan standalone LAN port configuration.

    Builds unit mappings from input units, fetches venue APs,
    and returns validation result for user confirmation.
    """

    class Inputs(BaseModel):
        units: List[Dict[str, Any]]

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = Field(
            default_factory=dict
        )
        validation_result: Optional[ValidationResult] = None
        all_venue_aps: List[Dict[str, Any]] = Field(
            default_factory=list
        )

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Build unit mappings and fetch venue APs."""
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

        # Count total APs to configure
        total_aps = sum(
            len(m.plan.ap_serial_numbers)
            for m in unit_mappings.values()
        )

        validation_result = ValidationResult(
            valid=True,
            summary=ValidationSummary(
                total_units=len(unit_mappings),
                total_api_calls=total_aps,
            ),
        )

        await self.emit(
            f"Ready to configure LAN ports on {total_aps} APs "
            f"across {len(unit_mappings)} units",
            "success",
        )

        return self.Outputs(
            unit_mappings=unit_mappings,
            validation_result=validation_result,
            all_venue_aps=all_venue_aps,
        )
