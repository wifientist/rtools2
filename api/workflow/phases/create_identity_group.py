"""
V2 Phase: Create Identity Group

Creates an identity group in RuckusONE for one unit.
Identity groups are required before DPSK pools can be created.

Supports shared pool model:
- will_create_identity_group=True: This unit creates the shared group
- will_create_identity_group=False: This unit looks up existing shared group

Idempotent - reuses existing groups with exact name matches.
"""

import asyncio
import logging
from pydantic import BaseModel
from typing import Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.v2.models import ResourceAction
from workflow.idempotent import IdempotentHelper

logger = logging.getLogger(__name__)


@register_phase("create_identity_group", "Create Identity Group")
class CreateIdentityGroupPhase(PhaseExecutor):
    """
    Create an identity group for a single unit.

    Supports shared pool model where only the first unit creates the group
    and subsequent units look it up.

    Finds existing identity group by exact name match, or creates a new one.
    Outputs the identity_group_id for downstream DPSK pool creation.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        identity_group_name: str
        # For shared pool model: only first unit creates, others lookup
        will_create_identity_group: bool = True

    class Outputs(BaseModel):
        identity_group_id: str
        reused: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Find or create an identity group for this unit."""
        # Shared pool model: if this unit doesn't create, just look it up
        if not inputs.will_create_identity_group:
            await self.emit(
                f"[{inputs.unit_number}] Looking up shared identity group "
                f"'{inputs.identity_group_name}'..."
            )
            group_id = await self._find_existing_group(inputs.identity_group_name)
            if group_id:
                await self.emit(
                    f"[{inputs.unit_number}] Using shared "
                    f"'{inputs.identity_group_name}'", "success"
                )
                return self.Outputs(identity_group_id=group_id, reused=True)

            # Group not found - might not be created yet (race condition)
            # Wait and retry a few times
            for attempt in range(5):
                await asyncio.sleep(1)
                group_id = await self._find_existing_group(
                    inputs.identity_group_name
                )
                if group_id:
                    await self.emit(
                        f"[{inputs.unit_number}] Found shared "
                        f"'{inputs.identity_group_name}'", "success"
                    )
                    return self.Outputs(identity_group_id=group_id, reused=True)

            raise RuntimeError(
                f"Shared identity group '{inputs.identity_group_name}' not found. "
                f"The creating unit may have failed."
            )

        await self.emit(
            f"[{inputs.unit_number}] Checking identity group "
            f"'{inputs.identity_group_name}'..."
        )

        helper = IdempotentHelper(self.r1_client)

        result = await helper.find_or_create_identity_group(
            tenant_id=self.tenant_id,
            name=inputs.identity_group_name,
            description=f"Identity group for unit {inputs.unit_number}",
            venueId=self.venue_id,
        )

        identity_group_id = result.get('id')
        if not identity_group_id:
            raise RuntimeError(
                f"Failed to create identity group "
                f"'{inputs.identity_group_name}' - no ID returned"
            )

        existed = result.get('existed', False)

        if existed:
            logger.info(
                f"[{inputs.unit_number}] Identity group "
                f"'{inputs.identity_group_name}' already exists "
                f"(ID: {identity_group_id})"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{inputs.identity_group_name}' "
                f"already exists"
            )
        else:
            await self.track_resource('identity_groups', {
                'id': identity_group_id,
                'name': inputs.identity_group_name,
                'unit_number': inputs.unit_number,
            })
            logger.info(
                f"[{inputs.unit_number}] Created identity group "
                f"'{inputs.identity_group_name}' (ID: {identity_group_id})"
            )
            await self.emit(
                f"[{inputs.unit_number}] Created "
                f"'{inputs.identity_group_name}'", "success"
            )

        return self.Outputs(
            identity_group_id=identity_group_id,
            reused=existed,
        )

    async def _find_existing_group(self, name: str) -> Optional[str]:
        """Look up existing identity group by name."""
        try:
            existing = await self.r1_client.identity.query_identity_groups(
                tenant_id=self.tenant_id,
                search_string=name,
            )
            items = existing.get('content', existing.get('data', []))
            for item in items:
                if item.get('name') == name:
                    return item.get('id')
        except Exception as e:
            logger.warning(f"Error looking up identity group: {e}")
        return None

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Check if identity group already exists."""
        helper = IdempotentHelper(self.r1_client)

        # Query existing identity groups to check
        try:
            existing = await self.r1_client.identity.query_identity_groups(
                tenant_id=self.tenant_id
            )
            items = existing.get('content', existing.get('data', []))
            match = next(
                (ig for ig in items
                 if ig.get('name') == inputs.identity_group_name),
                None
            )
        except Exception:
            match = None

        if match:
            return PhaseValidation(
                valid=True,
                will_create=False,
                will_reuse=True,
                existing_resource_id=match.get('id'),
                estimated_api_calls=0,
                actions=[ResourceAction(
                    resource_type="identity_group",
                    name=inputs.identity_group_name,
                    action="reuse",
                    existing_id=match.get('id'),
                )],
            )

        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=1,
            actions=[ResourceAction(
                resource_type="identity_group",
                name=inputs.identity_group_name,
                action="create",
            )],
        )
