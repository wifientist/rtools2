"""
Cloudpath Phase: Activate Network Venue-Wide

Activates a WiFi network on all AP groups (venue-wide).
Used for Scenario A (property-wide single SSID) where there is no
per-unit AP group to target.

This is separate from the shared activate_network phase which requires
an ap_group_id for the 3-step per-AP-group config. That phase is used
by the Per-Unit SSID tool and must not be modified.
"""

import logging
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


@register_phase("activate_venue_wide", "Activate Network Venue-Wide")
class ActivateVenueWidePhase(PhaseExecutor):
    """
    Activate a WiFi network venue-wide (all AP groups).

    For property-wide SSIDs that should broadcast everywhere.
    Only does Step 1 of the activation process — no AP group targeting.

    Idempotent — handles already-activated gracefully.
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
        """Activate SSID venue-wide."""
        display_name = inputs.ssid_name or inputs.network_id

        await self.emit(
            f"[{inputs.unit_number}] Activating '{display_name}' venue-wide..."
        )

        use_activity_tracker = self.context.activity_tracker is not None

        try:
            result = await self.r1_client.venues.activate_ssid_on_venue(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                wifi_network_id=inputs.network_id,
                wait_for_completion=not use_activity_tracker,
            )

            # If using tracker, wait via centralized polling
            if use_activity_tracker and result:
                request_id = (
                    result.get('requestId')
                    if isinstance(result, dict) else None
                )
                if request_id:
                    activity_result = await self.fire_and_wait(request_id)
                    if not activity_result.success:
                        raise RuntimeError(
                            f"Venue activation failed: {activity_result.error}"
                        )

            logger.info(
                f"[{inputs.unit_number}] SSID '{display_name}' "
                f"activated venue-wide"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' activated "
                f"venue-wide",
                "success",
            )
            return self.Outputs(activated=True, already_active=False)

        except Exception as e:
            error_str = str(e).lower()
            if (
                'already activated' in error_str
                or 'already exists' in error_str
            ):
                logger.info(
                    f"[{inputs.unit_number}] SSID '{display_name}' "
                    f"already activated venue-wide"
                )
                await self.emit(
                    f"[{inputs.unit_number}] '{display_name}' "
                    f"already activated"
                )
                return self.Outputs(activated=True, already_active=True)
            raise

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate activation inputs."""
        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=1,
            notes=["Venue-wide activation (all AP groups)"],
        )
