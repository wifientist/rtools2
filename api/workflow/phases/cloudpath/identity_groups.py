"""
V2 Phase: Create Identity Groups for Cloudpath Import

Creates identity groups for DPSK passphrases.
- Property-wide mode: Creates single identity group
- Per-unit mode: Creates one identity group per unit
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


@register_phase("create_identity_groups", "Create Identity Groups")
class CreateIdentityGroupsPhase(PhaseExecutor):
    """Create identity group(s) for DPSK passphrases."""

    class Inputs(BaseModel):
        import_mode: str = "property_wide"
        identity_group_name: Optional[str] = None  # From unit.plan (per-unit)
        identity_groups: List[Dict[str, Any]] = Field(
            default_factory=list,
            description="Groups to create (property-wide)"
        )
        # From unit input_config
        unit_number: Optional[str] = None
        # From unit.plan - controls whether this unit should create the group
        # In B1 scenario, only the first unit creates the shared group
        will_create_identity_group: bool = True

    class Outputs(BaseModel):
        identity_group_id: Optional[str] = None  # Per-unit
        identity_group_ids: Dict[str, str] = Field(
            default_factory=dict,
            description="Map of group names to IDs"
        )
        reused: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Create identity group(s)."""
        import asyncio

        # Check if this unit should create the group (B1 scenario: only first unit creates)
        if not inputs.will_create_identity_group and inputs.identity_group_name:
            # This unit uses a shared group created by another unit
            # Look up the existing group by name with exponential backoff
            # Total wait: 2+4+8+16+30+30 = 90 seconds max
            backoff_delays = [2, 4, 8, 16, 30, 30]

            await self.emit(
                f"Looking up shared identity group: {inputs.identity_group_name}"
            )

            for attempt, delay in enumerate(backoff_delays):
                existing = await self._find_existing_group(inputs.identity_group_name)
                if existing:
                    group_id = existing.get('id')
                    if attempt > 0:
                        await self.emit(
                            f"Found shared identity group after {attempt + 1} attempts",
                            "success"
                        )
                    else:
                        await self.emit(
                            f"Using shared identity group: {inputs.identity_group_name}",
                            "success"
                        )
                    return self.Outputs(
                        identity_group_id=group_id,
                        identity_group_ids={inputs.identity_group_name: group_id},
                        reused=True,
                    )

                # Not found yet - wait and retry
                if attempt < len(backoff_delays) - 1:
                    logger.debug(
                        f"Identity group '{inputs.identity_group_name}' not found, "
                        f"retry {attempt + 1}/{len(backoff_delays)} in {delay}s"
                    )
                    await asyncio.sleep(delay)

            # Final attempt failed
            raise RuntimeError(
                f"Shared identity group '{inputs.identity_group_name}' not found after "
                f"{len(backoff_delays)} attempts (~90s). The creating unit may have failed."
            )

        # Determine what to create
        if inputs.identity_group_name:
            # Per-unit mode: create single group for this unit
            groups_to_create = [{'name': inputs.identity_group_name}]
        elif inputs.identity_groups:
            # Property-wide mode: use groups from validation
            groups_to_create = inputs.identity_groups
        else:
            await self.emit("No identity groups to create")
            return self.Outputs()

        created_ids: Dict[str, str] = {}
        first_id: Optional[str] = None

        for group_config in groups_to_create:
            group_name = group_config.get('name')
            if not group_name:
                continue

            await self.emit(f"Creating identity group: {group_name}")

            try:
                # Check if group already exists
                existing = await self._find_existing_group(group_name)
                if existing:
                    group_id = existing.get('id')
                    await self.emit(f"Reusing existing identity group: {group_name}")
                    created_ids[group_name] = group_id
                    if not first_id:
                        first_id = group_id
                    continue

                # Create new group
                result = await self.r1_client.identity.create_identity_group(
                    name=group_name,
                    description=f"Cloudpath Import - {group_name}",
                    tenant_id=self.tenant_id,
                )

                group_id = result.get('id') if isinstance(result, dict) else None
                if group_id:
                    created_ids[group_name] = group_id
                    if not first_id:
                        first_id = group_id

                    await self.track_resource('identity_groups', {
                        'id': group_id,
                        'name': group_name,
                    })

                    await self.emit(f"Created identity group: {group_name}", "success")

            except Exception as e:
                await self.emit(f"Failed to create identity group {group_name}: {e}", "error")
                raise

        return self.Outputs(
            identity_group_id=first_id,
            identity_group_ids=created_ids,
            reused=len(created_ids) > 0 and not first_id,
        )

    async def _find_existing_group(self, name: str) -> Optional[Dict[str, Any]]:
        """Check if an identity group with this name exists."""
        try:
            response = await self.r1_client.identity.query_identity_groups(
                tenant_id=self.tenant_id,
                search_string=name,
            )
            groups = response.get('content', response.get('data', []))
            for group in groups:
                if group.get('name') == name:
                    return group
        except Exception as e:
            logger.warning(f"Error checking for existing identity group: {e}")
        return None

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate identity group creation."""
        count = 1 if inputs.identity_group_name else len(inputs.identity_groups)
        return PhaseValidation(
            valid=True,
            will_create=count > 0,
            estimated_api_calls=count * 2,  # Check + create
            notes=[f"{count} identity group(s) to create"],
        )
