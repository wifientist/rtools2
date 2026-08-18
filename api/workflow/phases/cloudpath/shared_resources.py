"""
V2 Phase: Create Shared Identity Group + DPSK Pool (GLOBAL)

Runs ONCE, before any per-unit work. Every ssid_mode ("none", "single",
"per_unit") shares one identity group and one DPSK pool across all units,
so this phase always runs.

Why this exists
---------------
Previously the first unit created these resources and every other unit
polled for them by name. Units run concurrently and the creator was not
guaranteed to be scheduled first, which deadlocked: followers occupied
every concurrency slot while sleeping in their backoff, so the one unit
allowed to create the group could never be scheduled. A run would end
with the creator still PENDING and dozens of units failed on
"identity group not found".

Creating the shared resources in a global phase removes the race by
construction — the resources provably exist before any unit starts, so
no unit ever polls and no unit ever waits on another unit.

R1 permits a pool to carry up to 5 identity groups, but an identity group
may belong to only ONE pool (DPSK-10032) — which is why one group backing
one pool is the only topology this workflow needs.
"""

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from r1api.constants import DpskPassphraseFormat
from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.cloudpath.validate import CloudpathPoolConfig

logger = logging.getLogger(__name__)

# Cloudpath phrase type -> RuckusONE passphrase format
PASSPHRASE_FORMAT_MAP = {
    'NUMERIC': DpskPassphraseFormat.NUMBERS_ONLY,
    'ALPHANUMERIC_MIXED': DpskPassphraseFormat.KEYBOARD_FRIENDLY,
    'ALPHANUMERIC_LOWER': DpskPassphraseFormat.KEYBOARD_FRIENDLY,
    'ALPHANUMERIC_UPPER': DpskPassphraseFormat.KEYBOARD_FRIENDLY,
    'COMPLEX': DpskPassphraseFormat.MOST_SECURED,
}


def resolve_passphrase_format(pool_config) -> str:
    """
    Pick the R1 passphrase format for a pool.

    An explicit import-time override wins; otherwise fall back to translating
    whatever Cloudpath exported. Shared by both pool-creation phases so the
    shared-pool and isolated paths cannot drift apart.
    """
    if pool_config.passphrase_format_override:
        return pool_config.passphrase_format_override
    return PASSPHRASE_FORMAT_MAP.get(
        pool_config.phrase_type, DpskPassphraseFormat.KEYBOARD_FRIENDLY
    )


@register_phase("create_shared_resources", "Create Shared Identity Group & DPSK Pool")
class CreateSharedResourcesPhase(PhaseExecutor):
    """Create (or reuse) the one identity group and one DPSK pool."""

    class Inputs(BaseModel):
        identity_groups: List[Dict[str, Any]] = Field(default_factory=list)
        dpsk_pools: List[Dict[str, Any]] = Field(default_factory=list)
        pool_config: Optional[CloudpathPoolConfig] = None

    class Outputs(BaseModel):
        identity_group_id: Optional[str] = None
        dpsk_pool_id: Optional[str] = None
        identity_group_ids: Dict[str, str] = Field(default_factory=dict)
        dpsk_pool_ids: Dict[str, str] = Field(default_factory=dict)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        if not inputs.identity_groups:
            await self.emit("No shared identity group planned")
            return self.Outputs()

        ig_name = inputs.identity_groups[0].get('name')
        if not ig_name:
            await self.emit("Shared identity group has no name", "error")
            return self.Outputs()

        # ---- Identity group -------------------------------------------------
        ig_id = await self._find_group(ig_name)
        if ig_id:
            await self.emit(f"Reusing identity group: {ig_name}", "success")
        else:
            await self.emit(f"Creating identity group: {ig_name}")
            ig_id = await self._create_group(ig_name)
            if not ig_id:
                raise RuntimeError(
                    f"Failed to create shared identity group '{ig_name}'"
                )
            await self.track_resource('identity_groups', {
                'id': ig_id, 'name': ig_name,
            })
            await self.emit(f"Created identity group: {ig_name}", "success")

        # ---- DPSK pool ------------------------------------------------------
        pool_id = None
        pool_name = None
        if inputs.dpsk_pools:
            pool_name = inputs.dpsk_pools[0].get('name')

        if pool_name:
            pool_config = inputs.pool_config or CloudpathPoolConfig(
                name="Cloudpath Import"
            )
            pool_id = await self._find_pool(pool_name)
            if pool_id:
                await self.emit(f"Reusing DPSK pool: {pool_name}", "success")
                # Widen the pool's minimum passphrase length if this import
                # carries shorter passphrases than the pool currently allows.
                await self._relax_passphrase_length(pool_id, pool_config)
            else:
                await self.emit(f"Creating DPSK pool: {pool_name}")
                pool_id = await self._create_pool(pool_name, ig_id, pool_config)
                if not pool_id:
                    raise RuntimeError(
                        f"Failed to create shared DPSK pool '{pool_name}'"
                    )
                await self.track_resource('dpsk_pools', {
                    'id': pool_id,
                    'name': pool_name,
                    'identity_group_id': ig_id,
                })
                await self.emit(f"Created DPSK pool: {pool_name}", "success")

        await self.emit(
            f"Shared resources ready — every unit can now proceed without waiting",
            "success",
        )

        return self.Outputs(
            identity_group_id=ig_id,
            dpsk_pool_id=pool_id,
            identity_group_ids={ig_name: ig_id} if ig_id else {},
            dpsk_pool_ids={pool_name: pool_id} if pool_id and pool_name else {},
        )

    # =========================================================================
    # Identity group
    # =========================================================================

    async def _find_group(self, name: str) -> Optional[str]:
        """
        Look up an identity group by exact name.

        Note: /identityGroups/query ignores searchString (verified against a
        live tenant — every search term returns the full set), so the filter
        has to happen here rather than server-side.
        """
        try:
            response = await self.r1_client.identity.query_identity_groups(
                tenant_id=self.tenant_id,
            )
            groups = response.get('content', response.get('data', []))
            for g in groups:
                if g.get('name') == name:
                    return g.get('id')
        except Exception as e:
            logger.warning(f"Error looking up identity group '{name}': {e}")
        return None

    async def _create_group(self, name: str) -> Optional[str]:
        """Create an identity group, waiting on the async activity."""
        import asyncio

        use_tracker = self.context.activity_tracker is not None
        result = await self.r1_client.identity.create_identity_group(
            name=name,
            description=f"Cloudpath Import - {name}",
            tenant_id=self.tenant_id,
            wait_for_completion=not use_tracker,
        )

        group_id = result.get('id') if isinstance(result, dict) else None
        if group_id:
            return group_id

        if use_tracker and isinstance(result, dict):
            request_id = result.get('requestId')
            if request_id:
                activity = await self.fire_and_wait(request_id)
                if not activity.success:
                    raise RuntimeError(
                        f"Identity group creation failed: {activity.error}"
                    )

        # R1 is eventually consistent — resolve by name with backoff.
        for attempt in range(6):
            group_id = await self._find_group(name)
            if group_id:
                return group_id
            delay = 1.0 * (2 ** attempt)  # 1,2,4,8,16,32
            logger.info(
                f"Identity group '{name}' not visible yet, retry in {delay}s"
            )
            await asyncio.sleep(delay)
        return None

    # =========================================================================
    # DPSK pool
    # =========================================================================

    async def _find_pool(self, name: str) -> Optional[str]:
        try:
            response = await self.r1_client.dpsk.query_dpsk_pools(
                tenant_id=self.tenant_id,
            )
            pools = (
                response if isinstance(response, list)
                else response.get('content', response.get('data', []))
            )
            for p in pools:
                if p.get('name') == name:
                    return p.get('id')
        except Exception as e:
            logger.warning(f"Error looking up DPSK pool '{name}': {e}")
        return None

    async def _relax_passphrase_length(
        self, pool_id: str, pool_config: CloudpathPoolConfig
    ) -> None:
        try:
            response = await self.r1_client.dpsk.get_dpsk_pool(
                pool_id, self.tenant_id
            )
            existing_length = (response or {}).get('passphraseLength', 12)
            if pool_config.phrase_length < existing_length:
                await self.emit(
                    f"Lowering pool minimum passphrase length "
                    f"{existing_length} -> {pool_config.phrase_length}"
                )
                await self.r1_client.dpsk.update_dpsk_pool(
                    pool_id=pool_id,
                    tenant_id=self.tenant_id,
                    passphrase_length=pool_config.phrase_length,
                )
        except Exception as e:
            logger.warning(f"Could not adjust pool passphrase length: {e}")

    async def _create_pool(
        self, name: str, identity_group_id: str, pool_config: CloudpathPoolConfig
    ) -> Optional[str]:
        import asyncio

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
            name=name,
            identity_group_id=identity_group_id,
            tenant_id=self.tenant_id,
            description=f"Cloudpath Import - {name}",
            passphrase_length=pool_config.phrase_length,
            passphrase_format=resolve_passphrase_format(pool_config),
            max_devices_per_passphrase=(
                pool_config.device_limit if pool_config.device_limit_enabled else 0
            ),
            expiration_days=expiration_days,
            word_count=pool_config.word_count,
            numeric_suffix_enabled=pool_config.numeric_suffix_enabled,
        )

        pool_id = result.get('id') if isinstance(result, dict) else None
        if pool_id:
            return pool_id

        # Async create — settle, then resolve by name.
        for attempt in range(6):
            pool_id = await self._find_pool(name)
            if pool_id:
                return pool_id
            await asyncio.sleep(1.0 * (2 ** attempt))
        return None

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(
            valid=True,
            will_create=bool(inputs.identity_groups),
            estimated_api_calls=4,
            notes=["Shared identity group + DPSK pool, created once up front"],
        )
