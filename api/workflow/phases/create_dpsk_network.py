"""
V2 Phase: Create DPSK Network

Creates a DPSK WiFi network in RuckusONE linked to a DPSK pool/service.
After creation, links the DPSK service to the network (required step).

Handles existing networks with conflict resolution (keep/overwrite).

Key difference from PSK: DPSK networks require:
1. Create network with type='dpsk' and dpskServiceId
2. Link DPSK service to the network (PUT endpoint)
"""

import logging
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.v2.models import ResourceAction

logger = logging.getLogger(__name__)


@register_phase("create_dpsk_network", "Create DPSK Network")
class CreateDPSKNetworkPhase(PhaseExecutor):
    """
    Create a DPSK WiFi network for a single unit.

    Checks for existing SSIDs and network names, handles conflicts,
    creates DPSK network, and links DPSK service.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_name: str
        ssid_name: str
        dpsk_pool_id: str  # DPSK pool/service ID (same thing in R1)
        default_vlan: str = "1"
        name_conflict_resolution: str = "keep"

        def get_vlan_id(self) -> int:
            """Safely parse VLAN ID with validation."""
            try:
                vlan = int(self.default_vlan)
                if not 1 <= vlan <= 4094:
                    raise ValueError(f"VLAN {vlan} out of range (1-4094)")
                return vlan
            except ValueError as e:
                raise ValueError(
                    f"Invalid VLAN '{self.default_vlan}' - expected number 1-4094. "
                    f"Check CSV column mapping. Error: {e}"
                )

    class Outputs(BaseModel):
        network_id: str
        reused: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Find or create a DPSK WiFi network for this unit."""
        await self.emit(
            f"[{inputs.unit_number}] Checking DPSK network "
            f"'{inputs.ssid_name}'..."
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

        # Both SSID and name are available - create DPSK network
        await self.emit(
            f"[{inputs.unit_number}] Creating DPSK network "
            f"'{inputs.network_name}'..."
        )

        # Use ActivityTracker for bulk polling if available
        use_activity_tracker = self.context.activity_tracker is not None

        result = await self.r1_client.networks.create_dpsk_wifi_network(
            tenant_id=self.tenant_id,
            venue_id=self.venue_id,
            name=inputs.network_name,
            ssid=inputs.ssid_name,
            dpsk_service_id=inputs.dpsk_pool_id,
            vlan_id=inputs.get_vlan_id(),
            description=f"Per-unit DPSK SSID for unit {inputs.unit_number}",
            wait_for_completion=not use_activity_tracker,
        )

        # If using tracker, wait via centralized polling
        if use_activity_tracker and result:
            request_id = result.get('requestId')
            if request_id:
                activity_result = await self.fire_and_wait(request_id)
                if not activity_result.success:
                    raise RuntimeError(
                        f"Failed to create DPSK network: {activity_result.error}"
                    )
                # Re-fetch the network to get its ID
                result = await self.r1_client.networks.find_wifi_network_by_ssid(
                    self.tenant_id, self.venue_id, inputs.ssid_name
                )

        network_id = result.get('id') if result else None
        if not network_id:
            raise RuntimeError(
                f"Failed to create DPSK network "
                f"'{inputs.network_name}' - no ID returned"
            )

        # Link DPSK service to the network
        await self._link_dpsk_service(network_id, inputs)

        await self.track_resource('wifi_networks', {
            'id': network_id,
            'name': inputs.network_name,
            'ssid': inputs.ssid_name,
            'unit_number': inputs.unit_number,
            'dpsk_pool_id': inputs.dpsk_pool_id,
            'type': 'dpsk',
        })

        logger.info(
            f"[{inputs.unit_number}] Created DPSK network "
            f"'{inputs.network_name}' (ID: {network_id}) "
            f"linked to DPSK pool {inputs.dpsk_pool_id}"
        )
        await self.emit(
            f"[{inputs.unit_number}] Created '{inputs.network_name}'",
            "success"
        )

        return self.Outputs(network_id=network_id, reused=False)

    async def _link_dpsk_service(
        self,
        network_id: str,
        inputs: 'Inputs',
    ) -> None:
        """
        Link DPSK service to the WiFi network.

        This is a REQUIRED step - R1 creates the network shell but doesn't
        automatically link the DPSK service. We must call the PUT endpoint.

        This operation is idempotent - calling it on an already-linked network
        will succeed without issues.
        """
        use_activity_tracker = self.context.activity_tracker is not None

        await self.emit(
            f"[{inputs.unit_number}] Linking DPSK service {inputs.dpsk_pool_id}..."
        )
        logger.info(
            f"[{inputs.unit_number}] Linking DPSK service {inputs.dpsk_pool_id} "
            f"to network {network_id}"
        )

        link_result = await self.r1_client.networks.activate_dpsk_service_on_network(
            tenant_id=self.tenant_id,
            network_id=network_id,
            dpsk_service_id=inputs.dpsk_pool_id,
            wait_for_completion=not use_activity_tracker,
        )

        # If using tracker, wait via centralized polling
        if use_activity_tracker and link_result:
            request_id = link_result.get('requestId') if isinstance(link_result, dict) else None
            if request_id:
                logger.info(
                    f"[{inputs.unit_number}] Waiting for DPSK link activity {request_id}"
                )
                activity_result = await self.fire_and_wait(request_id)
                if not activity_result.success:
                    raise RuntimeError(
                        f"Failed to link DPSK service: {activity_result.error}"
                    )
            else:
                logger.info(
                    f"[{inputs.unit_number}] DPSK link completed synchronously "
                    f"(no requestId)"
                )
        else:
            logger.info(
                f"[{inputs.unit_number}] DPSK link completed "
                f"(wait_for_completion={not use_activity_tracker})"
            )

        await self.emit(
            f"[{inputs.unit_number}] DPSK service linked successfully"
        )

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
        elif inputs.name_conflict_resolution == 'keep':
            # SSID exists but network name differs - keep R1 name
            logger.info(
                f"[{inputs.unit_number}] SSID '{inputs.ssid_name}' exists as "
                f"'{existing_name}' (keeping R1 name)"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{inputs.ssid_name}' exists "
                f"(R1 name: '{existing_name}')"
            )
        else:
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
                f"[{inputs.unit_number}] Renamed to '{inputs.network_name}'",
                "success"
            )

        # ALWAYS ensure DPSK service is linked, even for existing networks
        # This is idempotent - R1 handles already-linked services gracefully
        await self._link_dpsk_service(existing_id, inputs)

        return self.Outputs(network_id=existing_id, reused=True)

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Check if DPSK network/SSID already exists."""
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
            estimated_api_calls=2,  # create + link DPSK service
            actions=[ResourceAction(
                resource_type="wifi_network",
                name=inputs.network_name,
                action="create",
            )],
        )
