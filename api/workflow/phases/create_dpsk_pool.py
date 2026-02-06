"""
V2 Phase: Create DPSK Pool

Creates a DPSK pool in RuckusONE linked to an identity group.
DPSK pools contain the passphrases that authenticate devices.

Supports shared pool model:
- will_create_dpsk_pool=True: This unit creates the shared pool
- will_create_dpsk_pool=False: This unit looks up existing shared pool

Idempotent - reuses existing pools with the same name.
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


@register_phase("create_dpsk_pool", "Create DPSK Pool")
class CreateDPSKPoolPhase(PhaseExecutor):
    """
    Create a DPSK pool linked to an identity group for a single unit.

    Supports shared pool model where only the first unit creates the pool
    and subsequent units look it up.

    Finds existing pool by name, or creates a new one with the
    specified passphrase settings.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        dpsk_pool_name: str
        identity_group_id: str
        passphrase_length: int = 12
        passphrase_format: str = "KEYBOARD_FRIENDLY"
        max_devices_per_passphrase: int = 0  # 0 = unlimited
        expiration_days: Optional[int] = None
        # For shared pool model: only first unit creates, others lookup
        will_create_dpsk_pool: bool = True

    class Outputs(BaseModel):
        dpsk_pool_id: str
        reused: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Find or create a DPSK pool for this unit."""
        # Shared pool model: if this unit doesn't create, just look it up
        if not inputs.will_create_dpsk_pool:
            await self.emit(
                f"[{inputs.unit_number}] Looking up shared DPSK pool "
                f"'{inputs.dpsk_pool_name}'..."
            )
            pool_id = await self._find_existing_pool(inputs.dpsk_pool_name)
            if pool_id:
                await self.emit(
                    f"[{inputs.unit_number}] Using shared "
                    f"'{inputs.dpsk_pool_name}'", "success"
                )
                return self.Outputs(dpsk_pool_id=pool_id, reused=True)

            # Pool not found - might not be created yet (race condition)
            # Wait and retry a few times
            for attempt in range(5):
                await asyncio.sleep(1)
                pool_id = await self._find_existing_pool(inputs.dpsk_pool_name)
                if pool_id:
                    await self.emit(
                        f"[{inputs.unit_number}] Found shared "
                        f"'{inputs.dpsk_pool_name}'", "success"
                    )
                    return self.Outputs(dpsk_pool_id=pool_id, reused=True)

            raise RuntimeError(
                f"Shared DPSK pool '{inputs.dpsk_pool_name}' not found. "
                f"The creating unit may have failed."
            )

        await self.emit(
            f"[{inputs.unit_number}] Checking DPSK pool "
            f"'{inputs.dpsk_pool_name}'..."
        )

        helper = IdempotentHelper(self.r1_client)

        result = await helper.find_or_create_dpsk_pool(
            tenant_id=self.tenant_id,
            name=inputs.dpsk_pool_name,
            identity_group_id=inputs.identity_group_id,
            description=f"DPSK pool for unit {inputs.unit_number}",
            passphrase_length=inputs.passphrase_length,
            passphrase_format=inputs.passphrase_format,
            max_devices_per_passphrase=inputs.max_devices_per_passphrase,
            expiration_days=inputs.expiration_days,
        )

        dpsk_pool_id = result.get('id')
        if not dpsk_pool_id:
            raise RuntimeError(
                f"Failed to create DPSK pool "
                f"'{inputs.dpsk_pool_name}' - no ID returned"
            )

        existed = result.get('existed', False)

        if existed:
            logger.info(
                f"[{inputs.unit_number}] DPSK pool "
                f"'{inputs.dpsk_pool_name}' already exists "
                f"(ID: {dpsk_pool_id})"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{inputs.dpsk_pool_name}' "
                f"already exists"
            )
        else:
            await self.track_resource('dpsk_pools', {
                'id': dpsk_pool_id,
                'name': inputs.dpsk_pool_name,
                'unit_number': inputs.unit_number,
                'identity_group_id': inputs.identity_group_id,
            })
            logger.info(
                f"[{inputs.unit_number}] Created DPSK pool "
                f"'{inputs.dpsk_pool_name}' (ID: {dpsk_pool_id})"
            )
            await self.emit(
                f"[{inputs.unit_number}] Created "
                f"'{inputs.dpsk_pool_name}'", "success"
            )

        return self.Outputs(
            dpsk_pool_id=dpsk_pool_id,
            reused=existed,
        )

    async def _find_existing_pool(self, name: str) -> Optional[str]:
        """Look up existing DPSK pool by name."""
        try:
            existing = await self.r1_client.dpsk.query_dpsk_pools(
                tenant_id=self.tenant_id
            )
            items = (
                existing
                if isinstance(existing, list)
                else existing.get('content', existing.get('data', []))
            )
            for item in items:
                if item.get('name') == name:
                    return item.get('id')
        except Exception as e:
            logger.warning(f"Error looking up DPSK pool: {e}")
        return None

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """
        Check if DPSK pool already exists.

        Note: R1 DPSK pool query endpoint can be unreliable (500s),
        so we optimistically assume creation is needed on query failure.
        """
        try:
            existing = await self.r1_client.dpsk.query_dpsk_pools(
                tenant_id=self.tenant_id
            )
            items = existing if isinstance(existing, list) else existing.get('content', existing.get('data', []))
            match = next(
                (p for p in items
                 if p.get('name') == inputs.dpsk_pool_name),
                None
            )
        except Exception:
            # Query endpoint unreliable - assume creation needed
            match = None

        if match:
            return PhaseValidation(
                valid=True,
                will_create=False,
                will_reuse=True,
                existing_resource_id=match.get('id'),
                estimated_api_calls=0,
                actions=[ResourceAction(
                    resource_type="dpsk_pool",
                    name=inputs.dpsk_pool_name,
                    action="reuse",
                    existing_id=match.get('id'),
                )],
            )

        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=1,
            actions=[ResourceAction(
                resource_type="dpsk_pool",
                name=inputs.dpsk_pool_name,
                action="create",
            )],
        )
