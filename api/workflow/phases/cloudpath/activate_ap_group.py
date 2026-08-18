"""
V2 Phase: Activate Per-Unit SSID Directly on its AP Group

Binds a unit's SSID to that unit's AP Group without ever passing through an
"All AP Groups" state, using only non-deprecated endpoints:

    PUT /venues/{venueId}/wifiNetworks/{networkId}/settings
        isAllApGroups=false + the target AP group
    PUT /venues/{venueId}/wifiNetworks/{networkId}/apGroups/{apGroupId}
    PUT /venues/{venueId}/wifiNetworks/{networkId}/apGroups/{apGroupId}/settings

Why this replaces the old path
-----------------------------
The previous per-unit flow activated venue-wide first, then moved the SSID to
the unit's AP Group. Between those two steps the SSID broadcast on EVERY AP
Group in the venue and consumed one of R1's 15 SSIDs-per-AP-Group slots, so
concurrent activations had to be throttled to ~12 in flight. That throttle,
not the DPSK pool ceiling, is what made large properties slow.

Because this phase never creates a venue-wide state, it needs no activation
slot and imposes no in-flight cap.

The other single-step option, POST /networkActivations with
isAllApGroups=false, is deprecated with a stated removal date of 06/30/2026
that has already passed, so it is deliberately not used here.
"""

import logging
from typing import Optional

from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


@register_phase("activate_ap_group", "Activate SSID on AP Group")
class ActivateApGroupPhase(PhaseExecutor):
    """
    Activate a unit's SSID directly on its AP Group.

    Idempotent: an SSID already bound to a specific AP Group is left alone,
    and one still sitting venue-wide from an earlier run is pulled back onto
    its AP Group.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_id: str
        ap_group_id: str
        ap_group_name: str = ""
        ssid_name: str = ""
        default_vlan: str = "1"
        dpsk_pool_id: Optional[str] = None
        already_activated: bool = False
        is_venue_wide: bool = False

    class Outputs(BaseModel):
        activated: bool = True
        already_active: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Bind the SSID to this unit's AP Group."""
        display_name = inputs.ssid_name or inputs.network_id

        # Already on a specific AP group — nothing to do.
        if inputs.already_activated and not inputs.is_venue_wide:
            logger.info(
                f"[{inputs.unit_number}] SSID '{display_name}' already on a "
                f"specific AP group (skipping)"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' already activated "
                f"on AP Group"
            )
            return self.Outputs(activated=True, already_active=True)

        # The property-wide SSID in hybrid mode has no AP Group by design — it
        # is meant to reach every AP. Activate it across the venue instead of
        # binding it to a group. This is the one case where All AP Groups is
        # the correct outcome rather than something to avoid.
        if inputs.is_venue_wide and not inputs.ap_group_id:
            await self.emit(
                f"[{inputs.unit_number}] Activating property-wide SSID "
                f"'{display_name}' across the venue..."
            )
            await self.r1_client.venues.activate_ssid_on_venue(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                wifi_network_id=inputs.network_id,
                dpsk_service_id=inputs.dpsk_pool_id,
                wait_for_completion=True,
            )
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' activated venue-wide",
                "success",
            )
            return self.Outputs(activated=True, already_active=False)

        if not inputs.ap_group_id:
            raise RuntimeError(
                f"[{inputs.unit_number}] No AP Group ID — cannot activate "
                f"'{display_name}' without one. Check that create_ap_group "
                f"ran for this unit."
            )

        if inputs.already_activated and inputs.is_venue_wide:
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' is on All AP Groups, "
                f"moving to '{inputs.ap_group_name}'..."
            )
        else:
            await self.emit(
                f"[{inputs.unit_number}] Activating '{display_name}' on "
                f"AP Group '{inputs.ap_group_name}'..."
            )

        vlan_id = int(inputs.default_vlan) if inputs.default_vlan else None

        try:
            await self.r1_client.venues.activate_ssid_for_ap_group_direct(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                wifi_network_id=inputs.network_id,
                ap_group_id=inputs.ap_group_id,
                vlan_id=vlan_id,
                dpsk_service_id=inputs.dpsk_pool_id,
                already_activated=inputs.already_activated,
                wait_for_completion=True,
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
                    f"[{inputs.unit_number}] SSID '{display_name}' already "
                    f"activated (from API response)"
                )
                await self.emit(
                    f"[{inputs.unit_number}] '{display_name}' already activated"
                )
                return self.Outputs(activated=True, already_active=True)

            # The 3 PUTs are not atomic. If a later step failed the SSID may
            # still have landed correctly, so confirm before failing the unit.
            state = await self._check_ssid_venue_state(inputs)
            if state == 'specific':
                logger.warning(
                    f"[{inputs.unit_number}] Activation reported an error but "
                    f"the SSID IS on a specific AP group — treating as success"
                )
                await self.emit(
                    f"[{inputs.unit_number}] '{display_name}' verified on "
                    f"AP Group despite error: {e}",
                    "warning",
                )
                return self.Outputs(activated=True, already_active=False)

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
                if vag.get('venueId') != self.venue_id:
                    continue
                if vag.get('isAllApGroups', False):
                    logger.info(
                        f"[{inputs.unit_number}] Verify: '{display_name}' "
                        f"on All AP Groups (venue-wide)"
                    )
                    return 'venue_wide'
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
            logger.warning(f"[{inputs.unit_number}] Verify check failed: {e}")
            return 'not_found'

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate activation inputs."""
        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=3,
            notes=[
                "Direct AP Group activation (no venue-wide state, no slot limit)"
            ],
        )
