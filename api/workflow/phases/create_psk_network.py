"""
V2 Phase: Create PSK Network

Creates a standard WPA2/WPA3 WiFi network for one unit.
Handles existing networks with conflict resolution (keep/overwrite).

Supports two distinct name concepts:
- network_name: The internal R1 network name
- ssid_name: The broadcast SSID that clients see
"""

import logging
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.v2.models import ResourceAction

logger = logging.getLogger(__name__)


@register_phase("create_psk_network", "Create PSK Network")
class CreatePSKNetworkPhase(PhaseExecutor):
    """
    Create a PSK WiFi network for a single unit.

    Checks for existing SSIDs and network names, handles conflicts,
    and creates or reuses networks as appropriate.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_name: str
        ssid_name: str
        ssid_password: str
        security_type: str = "WPA3"
        default_vlan: str = "1"
        name_conflict_resolution: str = "keep"

    class Outputs(BaseModel):
        network_id: str
        reused: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Find or create a PSK WiFi network for this unit."""
        await self.emit(
            f"[{inputs.unit_number}] Checking SSID '{inputs.ssid_name}'..."
        )

        # Check if SSID (broadcast name) already exists
        existing_by_ssid = await self.r1_client.networks.find_wifi_network_by_ssid(
            self.tenant_id, self.venue_id, inputs.ssid_name
        )

        if existing_by_ssid:
            return await self._handle_existing_ssid(inputs, existing_by_ssid)

        # Check if network name is taken by a different SSID
        existing_by_name = await self.r1_client.networks.find_wifi_network_by_name(
            self.tenant_id, self.venue_id, inputs.network_name
        )

        if existing_by_name:
            existing_ssid = existing_by_name.get('ssid', 'unknown')
            raise RuntimeError(
                f"Network name '{inputs.network_name}' already in use "
                f"by SSID '{existing_ssid}'"
            )

        # Both SSID and name are available - create the network
        await self.emit(
            f"[{inputs.unit_number}] Creating '{inputs.network_name}'..."
        )

        # Use ActivityTracker for bulk polling if available
        use_activity_tracker = self.context.activity_tracker is not None

        result = await self.r1_client.networks.create_wifi_network(
            tenant_id=self.tenant_id,
            venue_id=self.venue_id,
            name=inputs.network_name,
            ssid=inputs.ssid_name,
            passphrase=inputs.ssid_password,
            security_type=inputs.security_type,
            vlan_id=int(inputs.default_vlan),
            description=f"Per-unit SSID for unit {inputs.unit_number}",
            wait_for_completion=not use_activity_tracker,
        )

        # If using tracker, wait via centralized polling
        if use_activity_tracker and result:
            request_id = result.get('requestId')
            if request_id:
                activity_result = await self.fire_and_wait(request_id)
                if not activity_result.success:
                    raise RuntimeError(
                        f"Failed to create network: {activity_result.error}"
                    )
                # Re-fetch the network to get its ID
                result = await self.r1_client.networks.find_wifi_network_by_ssid(
                    self.tenant_id, self.venue_id, inputs.ssid_name
                )

        network_id = result.get('id') if result else None
        if not network_id:
            raise RuntimeError(
                f"Failed to create network '{inputs.network_name}' - no ID returned"
            )

        await self.track_resource('wifi_networks', {
            'id': network_id,
            'name': inputs.network_name,
            'ssid': inputs.ssid_name,
            'unit_number': inputs.unit_number,
        })

        logger.info(
            f"[{inputs.unit_number}] Created network "
            f"'{inputs.network_name}' (ID: {network_id})"
        )
        await self.emit(
            f"[{inputs.unit_number}] Created '{inputs.network_name}'", "success"
        )

        return self.Outputs(network_id=network_id, reused=False)

    async def _handle_existing_ssid(
        self,
        inputs: 'Inputs',
        existing: dict,
    ) -> 'Outputs':
        """Handle case where SSID already exists in the venue."""
        existing_id = existing.get('id')
        existing_name = existing.get('name', 'unknown')

        # Perfect match - reuse as-is
        if existing_name == inputs.network_name:
            logger.info(
                f"[{inputs.unit_number}] SSID '{inputs.ssid_name}' exists "
                f"with matching name (ID: {existing_id})"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{inputs.ssid_name}' already exists"
            )
            return self.Outputs(network_id=existing_id, reused=True)

        # SSID exists but network name differs
        if inputs.name_conflict_resolution == 'keep':
            logger.info(
                f"[{inputs.unit_number}] SSID '{inputs.ssid_name}' exists as "
                f"'{existing_name}' (keeping R1 name)"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{inputs.ssid_name}' exists "
                f"(R1 name: '{existing_name}')"
            )
            return self.Outputs(network_id=existing_id, reused=True)

        # Overwrite - rename the network
        logger.info(
            f"[{inputs.unit_number}] Renaming '{existing_name}' -> "
            f"'{inputs.network_name}'"
        )
        await self.emit(
            f"[{inputs.unit_number}] Renaming '{existing_name}' -> "
            f"'{inputs.network_name}'"
        )

        await self.r1_client.networks.update_wifi_network_name(
            tenant_id=self.tenant_id,
            network_id=existing_id,
            new_name=inputs.network_name,
            wait_for_completion=True,
        )

        await self.emit(
            f"[{inputs.unit_number}] Renamed to '{inputs.network_name}'", "success"
        )
        return self.Outputs(network_id=existing_id, reused=True)

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Check if network/SSID already exists."""
        existing_by_ssid = await self.r1_client.networks.find_wifi_network_by_ssid(
            self.tenant_id, self.venue_id, inputs.ssid_name
        )

        if existing_by_ssid:
            return PhaseValidation(
                valid=True,
                will_create=False,
                will_reuse=True,
                existing_resource_id=existing_by_ssid.get('id'),
                estimated_api_calls=0,
                actions=[ResourceAction(
                    resource_type="wifi_network",
                    name=inputs.ssid_name,
                    action="reuse",
                    existing_id=existing_by_ssid.get('id'),
                )],
            )

        # Check for name conflict
        existing_by_name = await self.r1_client.networks.find_wifi_network_by_name(
            self.tenant_id, self.venue_id, inputs.network_name
        )

        if existing_by_name:
            existing_ssid = existing_by_name.get('ssid', 'unknown')
            return PhaseValidation(
                valid=False,
                errors=[
                    f"Network name '{inputs.network_name}' already in use "
                    f"by SSID '{existing_ssid}'"
                ],
            )

        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=1,
            actions=[ResourceAction(
                resource_type="wifi_network",
                name=inputs.network_name,
                action="create",
            )],
        )
