"""
V2 Phase: Activate Network on AP Group (Direct)

Activates a WiFi network (SSID) directly on a specific AP Group, never
putting it on All AP Groups, via venues.activate_ssid_for_ap_group_direct:
  1. PUT /venues/{v}/wifiNetworks/{n} (or .../settings when already on the
     venue) with isAllApGroups=false and this group
  2. PUT /venues/{v}/wifiNetworks/{n}/apGroups/{g}
  3. PUT /venues/{v}/wifiNetworks/{n}/apGroups/{g}/settings

Until 2026-11-11 this was one POST /networkActivations. R1 deprecated that
(removal allowed since 2026-08-31), so it moved to the same three supported
PUTs the Cloudpath import and the SZ migration already use. Still:
- No intermediate "All AP Groups" state
- No 15-SSID venue-wide slot pressure

The old venue-wide-then-move phase (activate_network.py) is kept as a fallback.
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

    Three ordered PUTs via venues.activate_ssid_for_ap_group_direct (was the
    deprecated POST /networkActivations until 2026-11-11).
    Idempotent - handles already-activated gracefully.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_id: str
        ap_group_id: str
        ap_group_name: str = ""
        ssid_name: str = ""
        # AP-Group VLAN OVERRIDE, empty means inherit.
        #
        # R1 treats vlanId on an AP Group activation as a per-group OVERRIDE
        # and shows it as "vlan-N (custom)", so sending one unconditionally
        # pinned every activation to a custom VLAN instead of the network's
        # own. With DPSK the per-resident VLAN rides on each passphrase, and a
        # VLAN pinned here overrides those.
        #
        # Deliberately NOT default_vlan: that one is the NETWORK's VLAN (and
        # the LAN port VLAN) and must stay set. Same split as the Cloudpath
        # import's activate_ap_group phase.
        ap_group_vlan: str = ""
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

        # None => omit vlanId => the AP Group inherits the network's VLAN,
        # which is what doing this by hand in R1 produces.
        vlan_id = (
            int(inputs.ap_group_vlan)
            if str(inputs.ap_group_vlan).strip()
            else None
        )

        try:
            # Three ordered PUTs (venue association with isAllApGroups=false and
            # this group, bind the group, group settings). Replaced the single
            # POST /networkActivations on 2026-11-11: deprecated, removal allowed
            # since 2026-08-31. Same result: the SSID goes straight to the group
            # and never onto All AP Groups. For an SSID already venue-wide,
            # already_activated routes step 1 to .../settings, which moves it
            # onto this group (what the POST did). Each step waits for the
            # previous one, as the Cloudpath import's activate_ap_group does.
            await self.r1_client.venues.activate_ssid_for_ap_group_direct(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                wifi_network_id=inputs.network_id,
                ap_group_id=inputs.ap_group_id,
                vlan_id=vlan_id,
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
            estimated_api_calls=3,
            notes=[
                "Direct activation to specific AP group (3 ordered PUTs, never All AP Groups)"
            ],
        )
