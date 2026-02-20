"""
V2 Phase: Create AP Group

Creates a single AP Group in RuckusONE for one unit.
Idempotent - reuses existing groups with exact name matches.

IMPORTANT: AP Groups should be created BEFORE SSIDs exist in the venue to avoid
the 15 SSID per AP Group limit issue. RuckusONE auto-activates all existing venue
SSIDs on new AP Groups.
"""

import asyncio
import logging
from pydantic import BaseModel
from typing import Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.v2.models import ResourceAction

logger = logging.getLogger(__name__)


@register_phase("create_ap_group", "Create AP Group")
class CreateAPGroupPhase(PhaseExecutor):
    """
    Create an AP Group for a single unit.

    Finds existing AP Group by exact name match, or creates a new one.
    Outputs the ap_group_id for downstream phases.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        ap_group_name: str
        # Pre-resolved by validation (skips individual R1 query)
        ap_group_id: Optional[str] = None

    class Outputs(BaseModel):
        ap_group_id: str
        reused: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Find or create an AP Group for this unit."""

        # Fast path: validation already found this AP group.
        # Skip the individual R1 query (saves 1 API call per unit).
        if inputs.ap_group_id:
            logger.info(
                f"[{inputs.unit_number}] AP Group '{inputs.ap_group_name}' "
                f"pre-resolved from validation (ID: {inputs.ap_group_id})"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{inputs.ap_group_name}' already exists"
            )
            return self.Outputs(ap_group_id=inputs.ap_group_id, reused=True)

        await self.emit(
            f"[{inputs.unit_number}] Checking AP Group '{inputs.ap_group_name}'..."
        )

        # Check if AP Group with exact name exists
        existing = await self.r1_client.venues.find_ap_group_by_name(
            self.tenant_id, self.venue_id, inputs.ap_group_name
        )

        if existing and existing.get('name') == inputs.ap_group_name:
            ap_group_id = existing.get('id')
            logger.info(
                f"[{inputs.unit_number}] AP Group '{inputs.ap_group_name}' "
                f"already exists (ID: {ap_group_id})"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{inputs.ap_group_name}' already exists"
            )
            return self.Outputs(ap_group_id=ap_group_id, reused=True)

        # Log partial match warning
        if existing:
            logger.info(
                f"[{inputs.unit_number}] Found '{existing.get('name')}' "
                f"but need exact '{inputs.ap_group_name}' - creating new"
            )

        # Create AP Group
        await self.emit(
            f"[{inputs.unit_number}] Creating '{inputs.ap_group_name}'..."
        )

        # Use ActivityTracker for bulk polling if available
        use_activity_tracker = self.context.activity_tracker is not None

        result = await self.r1_client.venues.create_ap_group(
            tenant_id=self.tenant_id,
            venue_id=self.venue_id,
            name=inputs.ap_group_name,
            description=f"AP Group for unit {inputs.unit_number}",
            wait_for_completion=not use_activity_tracker,
        )

        # If using tracker, wait via centralized polling
        if use_activity_tracker and result:
            request_id = result.get('requestId')
            if request_id:
                activity_result = await self.fire_and_wait(request_id)
                if not activity_result.success:
                    raise RuntimeError(
                        f"Failed to create AP Group: {activity_result.error}"
                    )
                # Re-fetch the AP group to get its ID
                # R1 has eventual consistency - retry with backoff
                ap_group_id = None
                for attempt in range(5):
                    result = await self.r1_client.venues.find_ap_group_by_name(
                        self.tenant_id, self.venue_id, inputs.ap_group_name
                    )
                    ap_group_id = result.get('id') if result else None
                    if ap_group_id:
                        break
                    delay = 1.0 * (2 ** attempt)  # 1s, 2s, 4s, 8s, 16s
                    logger.info(
                        f"[{inputs.unit_number}] AP Group not yet visible, "
                        f"retrying in {delay}s (attempt {attempt + 1}/5)"
                    )
                    await asyncio.sleep(delay)

                if not ap_group_id:
                    raise RuntimeError(
                        f"Failed to create AP Group '{inputs.ap_group_name}' - "
                        f"activity succeeded but group not found after retries"
                    )
        else:
            # Direct path (no activity tracker) - result should have ID
            ap_group_id = result.get('id') if result else None
            if not ap_group_id:
                raise RuntimeError(
                    f"Failed to create AP Group '{inputs.ap_group_name}' - no ID returned"
                )

        await self.track_resource('ap_groups', {
            'id': ap_group_id,
            'name': inputs.ap_group_name,
            'unit_number': inputs.unit_number,
        })

        logger.info(
            f"[{inputs.unit_number}] Created AP Group "
            f"'{inputs.ap_group_name}' (ID: {ap_group_id})"
        )
        await self.emit(
            f"[{inputs.unit_number}] Created '{inputs.ap_group_name}'", "success"
        )

        return self.Outputs(ap_group_id=ap_group_id, reused=False)

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Check if AP Group already exists."""
        existing = await self.r1_client.venues.find_ap_group_by_name(
            self.tenant_id, self.venue_id, inputs.ap_group_name
        )

        if existing and existing.get('name') == inputs.ap_group_name:
            return PhaseValidation(
                valid=True,
                will_create=False,
                will_reuse=True,
                existing_resource_id=existing.get('id'),
                estimated_api_calls=0,
                actions=[ResourceAction(
                    resource_type="ap_group",
                    name=inputs.ap_group_name,
                    action="reuse",
                    existing_id=existing.get('id'),
                )],
            )

        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=1,
            actions=[ResourceAction(
                resource_type="ap_group",
                name=inputs.ap_group_name,
                action="create",
            )],
        )
