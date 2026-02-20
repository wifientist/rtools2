"""
V2 Phase: Activate Network on AP Group (Direct)

Activates a WiFi network (SSID) directly on a specific AP Group using
the POST /networkActivations endpoint with isAllApGroups=false.

This is a single-step replacement for the old 3-step process:
  Old: activate venue-wide → settings → apGroups → apGroups/settings
  New: POST /networkActivations with apGroups=[{apGroupId: ...}]

Benefits:
- No intermediate "All AP Groups" state
- No 15-SSID venue-wide slot pressure
- No orphan cascade if the move step fails
- 1 API call instead of 4

The /networkActivations endpoint is deprecated in R1 but still active.
The old phase (activate_network.py) is kept as a fallback.
"""

import logging
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


@register_phase("activate_network_direct", "Activate Network (Direct)")
class ActivateNetworkDirectPhase(PhaseExecutor):
    """
    Activate a WiFi network directly on a specific AP Group.

    Single-step via POST /networkActivations.
    Idempotent - handles already-activated gracefully.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_id: str
        ap_group_id: str
        ap_group_name: str = ""
        ssid_name: str = ""
        default_vlan: str = "1"
        already_activated: bool = False
        is_venue_wide: bool = False

    class Outputs(BaseModel):
        activated: bool = True
        already_active: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Activate SSID directly on a specific AP Group."""
        display_name = inputs.ssid_name or inputs.network_id

        # Fast path: SSID is already on a specific AP group — skip entirely.
        if inputs.already_activated and not inputs.is_venue_wide:
            logger.info(
                f"[{inputs.unit_number}] SSID '{display_name}' "
                f"already on specific AP group (skipping)"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' already activated "
                f"on AP Group"
            )
            return self.Outputs(activated=True, already_active=True)

        # For venue-wide SSIDs, direct activation will move them to the
        # specific AP group. For new SSIDs, it activates directly.
        if inputs.already_activated and inputs.is_venue_wide:
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' on All AP Groups, "
                f"moving to '{inputs.ap_group_name}'..."
            )
        else:
            await self.emit(
                f"[{inputs.unit_number}] Activating '{display_name}' on "
                f"AP Group '{inputs.ap_group_name}'..."
            )

        use_activity_tracker = self.context.activity_tracker is not None
        vlan_id = int(inputs.default_vlan) if inputs.default_vlan else None

        try:
            result = await self.r1_client.venues.activate_network_direct(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                network_id=inputs.network_id,
                ap_group_id=inputs.ap_group_id,
                ap_group_name=inputs.ap_group_name,
                vlan_id=vlan_id,
                wait_for_completion=not use_activity_tracker,
            )

            # If using tracker, wait via centralized polling
            if use_activity_tracker and result:
                request_id = result.get('requestId') if isinstance(result, dict) else None
                if request_id:
                    activity_result = await self.fire_and_wait(request_id)
                    if not activity_result.success:
                        # Verify actual state before declaring failure
                        state = await self._check_ssid_venue_state(inputs)
                        if state == 'specific':
                            logger.warning(
                                f"[{inputs.unit_number}] Activity timed out but "
                                f"SSID IS on specific AP group — treating as success"
                            )
                        else:
                            raise RuntimeError(
                                f"Direct activation failed: {activity_result.error}"
                            )

            logger.info(
                f"[{inputs.unit_number}] SSID '{display_name}' activated on "
                f"AP Group '{inputs.ap_group_name}'"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' activated on "
                f"AP Group '{inputs.ap_group_name}'",
                "success",
            )
            return self.Outputs(activated=True, already_active=False)

        except Exception as e:
            error_str = str(e).lower()

            # Already activated — treat as success
            if 'already activated' in error_str or 'already exists' in error_str:
                logger.info(
                    f"[{inputs.unit_number}] SSID '{display_name}' "
                    f"already activated (from API response)"
                )
                await self.emit(
                    f"[{inputs.unit_number}] '{display_name}' already activated"
                )
                return self.Outputs(activated=True, already_active=True)

            raise

    async def _check_ssid_venue_state(self, inputs: 'Inputs') -> str:
        """
        Check the actual SSID activation state on the venue.

        Returns one of:
          'specific'   — on a specific AP group
          'venue_wide' — on All AP Groups (isAllApGroups=true)
          'not_found'  — not activated on this venue
        """
        display_name = inputs.ssid_name or inputs.network_id
        try:
            network = await self.r1_client.networks.query_wifi_network_by_id(
                inputs.network_id, self.tenant_id
            )
            for vag in network.get('venueApGroups', []):
                if vag.get('venueId') == self.venue_id:
                    is_all = vag.get('isAllApGroups', False)
                    if is_all:
                        logger.info(
                            f"[{inputs.unit_number}] Verify: '{display_name}' "
                            f"on All AP Groups (venue-wide)"
                        )
                        return 'venue_wide'
                    else:
                        logger.info(
                            f"[{inputs.unit_number}] Verify: '{display_name}' "
                            f"on specific AP group"
                        )
                        return 'specific'
            logger.info(
                f"[{inputs.unit_number}] Verify: '{display_name}' "
                f"not found on venue"
            )
            return 'not_found'
        except Exception as e:
            logger.warning(
                f"[{inputs.unit_number}] Verify check failed: {e}"
            )
            return 'not_found'

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate activation inputs."""
        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=1,
            notes=[
                "Direct activation to specific AP group (POST /networkActivations)"
            ],
        )
