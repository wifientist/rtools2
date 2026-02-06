"""
V2 Phase: Activate Network on Venue

Activates a WiFi network (SSID) at the venue level. This must happen
before the SSID can be configured on specific AP Groups.

Gracefully handles "already activated" responses from R1.
"""

import logging
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


@register_phase("activate_network", "Activate Network on Venue")
class ActivateNetworkPhase(PhaseExecutor):
    """
    Activate a WiFi network on the venue.

    Required before configuring SSID for specific AP Groups.
    Idempotent - handles already-activated gracefully.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_id: str
        ssid_name: str = ""

    class Outputs(BaseModel):
        activated: bool = True
        already_active: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Activate SSID on the venue."""
        display_name = inputs.ssid_name or inputs.network_id

        await self.emit(
            f"[{inputs.unit_number}] Activating '{display_name}' on venue..."
        )

        try:
            # Use ActivityTracker for bulk polling if available
            use_activity_tracker = self.context.activity_tracker is not None

            result = await self.r1_client.venues.activate_ssid_on_venue(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                wifi_network_id=inputs.network_id,
                wait_for_completion=not use_activity_tracker,
            )

            # If using tracker, wait via centralized polling
            if use_activity_tracker and result:
                request_id = result.get('requestId') if isinstance(result, dict) else None
                if request_id:
                    activity_result = await self.fire_and_wait(request_id)
                    if not activity_result.success:
                        raise RuntimeError(
                            f"Failed to activate SSID: {activity_result.error}"
                        )

            logger.info(
                f"[{inputs.unit_number}] SSID '{display_name}' activated on venue"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' activated", "success"
            )
            return self.Outputs(activated=True, already_active=False)

        except Exception as e:
            error_str = str(e).lower()
            # R1 returns an error if SSID is already activated - treat as success
            if 'already activated' in error_str or 'already exists' in error_str:
                logger.info(
                    f"[{inputs.unit_number}] SSID '{display_name}' "
                    f"already activated on venue"
                )
                await self.emit(
                    f"[{inputs.unit_number}] '{display_name}' already activated"
                )
                return self.Outputs(activated=True, already_active=True)
            raise

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Activation is always needed (idempotent, so always valid)."""
        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=1,
            notes=["Venue-level SSID activation (idempotent)"],
        )
