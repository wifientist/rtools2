"""
V2 Phase: Create DPSK Pools for Cloudpath Import

Creates DPSK service pools linked to identity groups.
- Property-wide mode: Creates single pool
- Per-unit mode: Creates one pool per unit
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.cloudpath.validate import CloudpathPoolConfig

logger = logging.getLogger(__name__)


@register_phase("create_dpsk_pools", "Create DPSK Pools")
class CreateDPSKPoolsPhase(PhaseExecutor):
    """Create DPSK service pool(s) linked to identity groups."""

    class Inputs(BaseModel):
        import_mode: str = "property_wide"
        dpsk_pool_name: Optional[str] = None  # From unit.plan (per-unit)
        identity_group_id: Optional[str] = None  # From unit.resolved (per-unit)
        pool_config: Optional[CloudpathPoolConfig] = None
        dpsk_pools: List[Dict[str, Any]] = Field(
            default_factory=list,
            description="Pools to create (property-wide)"
        )
        identity_group_ids: Dict[str, str] = Field(
            default_factory=dict,
            description="Map of group names to IDs"
        )
        # From unit input_config
        unit_number: Optional[str] = None
        # From unit.plan - controls whether this unit should create the pool
        # In B1 scenario, only the first unit creates the shared pool
        will_create_dpsk_pool: bool = True

    class Outputs(BaseModel):
        dpsk_pool_id: Optional[str] = None  # Per-unit
        dpsk_pool_ids: Dict[str, str] = Field(
            default_factory=dict,
            description="Map of pool names to IDs"
        )
        reused: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Create DPSK pool(s)."""
        import asyncio

        pool_config = inputs.pool_config or CloudpathPoolConfig(name="Cloudpath Import")

        # Check if this unit should create the pool (B1 scenario: only first unit creates)
        if not inputs.will_create_dpsk_pool and inputs.dpsk_pool_name:
            # This unit uses a shared pool created by another unit
            # Look up the existing pool by name with exponential backoff
            # Total wait: 2+4+8+16+30+30 = 90 seconds max
            backoff_delays = [2, 4, 8, 16, 30, 30]

            await self.emit(
                f"Looking up shared DPSK pool: {inputs.dpsk_pool_name}"
            )

            for attempt, delay in enumerate(backoff_delays):
                existing = await self._find_existing_pool(inputs.dpsk_pool_name)
                if existing:
                    pool_id = existing.get('id')
                    if attempt > 0:
                        await self.emit(
                            f"Found shared DPSK pool after {attempt + 1} attempts",
                            "success"
                        )
                    else:
                        await self.emit(
                            f"Using shared DPSK pool: {inputs.dpsk_pool_name}",
                            "success"
                        )
                    return self.Outputs(
                        dpsk_pool_id=pool_id,
                        dpsk_pool_ids={inputs.dpsk_pool_name: pool_id},
                        reused=True,
                    )

                # Not found yet - wait and retry
                if attempt < len(backoff_delays) - 1:
                    logger.debug(
                        f"DPSK pool '{inputs.dpsk_pool_name}' not found, "
                        f"retry {attempt + 1}/{len(backoff_delays)} in {delay}s"
                    )
                    await asyncio.sleep(delay)

            # Final attempt failed
            raise RuntimeError(
                f"Shared DPSK pool '{inputs.dpsk_pool_name}' not found after "
                f"{len(backoff_delays)} attempts (~90s). The creating unit may have failed."
            )

        # Determine what to create
        if inputs.dpsk_pool_name and inputs.identity_group_id:
            # Per-unit mode: create single pool for this unit
            pools_to_create = [{
                'name': inputs.dpsk_pool_name,
                'identity_group_id': inputs.identity_group_id,
            }]
        elif inputs.dpsk_pools:
            # Property-wide mode: use pools from validation
            pools_to_create = []
            for pool in inputs.dpsk_pools:
                ig_name = pool.get('identity_group_name')
                ig_id = inputs.identity_group_ids.get(ig_name)
                if ig_id:
                    pools_to_create.append({
                        'name': pool.get('name'),
                        'identity_group_id': ig_id,
                    })
        else:
            await self.emit("No DPSK pools to create")
            return self.Outputs()

        created_ids: Dict[str, str] = {}
        first_id: Optional[str] = None

        for pool_data in pools_to_create:
            pool_name = pool_data.get('name')
            identity_group_id = pool_data.get('identity_group_id')

            if not pool_name or not identity_group_id:
                continue

            await self.emit(f"Creating DPSK pool: {pool_name}")

            try:
                # Check if pool already exists
                existing = await self._find_existing_pool(pool_name)
                if existing:
                    pool_id = existing.get('id')
                    await self.emit(f"Reusing existing DPSK pool: {pool_name}")

                    # Check if we need to update the pool's passphrase length
                    # to accommodate shorter passphrases from the import
                    existing_length = existing.get('passphraseLength', 12)
                    if pool_config.phrase_length < existing_length:
                        await self.emit(
                            f"Updating pool passphrase min length from {existing_length} "
                            f"to {pool_config.phrase_length}"
                        )
                        try:
                            await self.r1_client.dpsk.update_dpsk_pool(
                                pool_id=pool_id,
                                tenant_id=self.tenant_id,
                                passphrase_length=pool_config.phrase_length,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to update pool passphrase length: {e}")

                    created_ids[pool_name] = pool_id
                    if not first_id:
                        first_id = pool_id
                    continue

                # Create new pool
                # Map Cloudpath phrase type to RuckusONE passphrase format
                # Cloudpath: ALPHANUMERIC_MIXED, NUMERIC, etc.
                # RuckusONE: NUMBERS_ONLY, KEYBOARD_FRIENDLY, MOST_SECURED
                format_map = {
                    'NUMERIC': 'NUMBERS_ONLY',
                    'ALPHANUMERIC_MIXED': 'KEYBOARD_FRIENDLY',
                    'ALPHANUMERIC_LOWER': 'KEYBOARD_FRIENDLY',
                    'ALPHANUMERIC_UPPER': 'KEYBOARD_FRIENDLY',
                    'COMPLEX': 'MOST_SECURED',
                }
                passphrase_format = format_map.get(
                    pool_config.phrase_type, 'KEYBOARD_FRIENDLY'
                )

                # Calculate expiration_days from Cloudpath settings
                expiration_days = None
                if pool_config.expiration_enabled:
                    try:
                        val = int(pool_config.expiration_value)
                        if pool_config.expiration_type == 'MONTHS_AFTER_TIME':
                            expiration_days = val * 30
                        elif pool_config.expiration_type == 'DAYS_AFTER_TIME':
                            expiration_days = val
                        elif pool_config.expiration_type == 'YEARS_AFTER_TIME':
                            expiration_days = val * 365
                    except (ValueError, TypeError):
                        pass

                result = await self.r1_client.dpsk.create_dpsk_pool(
                    name=pool_name,
                    identity_group_id=identity_group_id,
                    tenant_id=self.tenant_id,
                    description=f"Cloudpath Import - {pool_name}",
                    passphrase_length=pool_config.phrase_length,
                    passphrase_format=passphrase_format,
                    max_devices_per_passphrase=pool_config.device_limit if pool_config.device_limit_enabled else 0,
                    expiration_days=expiration_days,
                )

                pool_id = result.get('id') if isinstance(result, dict) else None
                if pool_id:
                    created_ids[pool_name] = pool_id
                    if not first_id:
                        first_id = pool_id

                    await self.track_resource('dpsk_pools', {
                        'id': pool_id,
                        'name': pool_name,
                        'identity_group_id': identity_group_id,
                    })

                    await self.emit(f"Created DPSK pool: {pool_name}", "success")

            except Exception as e:
                await self.emit(f"Failed to create DPSK pool {pool_name}: {e}", "error")
                raise

        return self.Outputs(
            dpsk_pool_id=first_id,
            dpsk_pool_ids=created_ids,
            reused=len(created_ids) > 0 and not first_id,
        )

    async def _find_existing_pool(self, name: str) -> Optional[Dict[str, Any]]:
        """Check if a DPSK pool with this name exists."""
        try:
            response = await self.r1_client.dpsk.query_dpsk_pools(
                tenant_id=self.tenant_id,
            )
            pools = response.get('content', response.get('data', []))
            if isinstance(response, list):
                pools = response
            for pool in pools:
                if pool.get('name') == name:
                    return pool
        except Exception as e:
            logger.warning(f"Error checking for existing DPSK pool: {e}")
        return None

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate DPSK pool creation."""
        count = 1 if inputs.dpsk_pool_name else len(inputs.dpsk_pools)
        return PhaseValidation(
            valid=True,
            will_create=count > 0,
            estimated_api_calls=count,
            notes=[f"{count} DPSK pool(s) to create"],
        )
