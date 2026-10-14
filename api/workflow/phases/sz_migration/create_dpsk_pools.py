"""
SZ → R1 Migration: Create DPSK Pools (Global)

Creates the DPSK services chosen on Review -- once each, before any network
exists -- and publishes pool_key -> pool_id for configure_dpsk to link.

Global rather than per-WLAN because several WLANs can share one new pool:
per-unit phases run concurrently, and two units creating the same pool
would race (the loser gets "Name must be unique" mid-run).

A plan built before Review offered DPSK choices has no "dpsk_pools" option;
this phase then does nothing and configure_dpsk keeps its per-WLAN
{wlan}-pool behaviour.
"""

import logging
from typing import Any, Dict

from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)


@register_phase("create_dpsk_pools", "Create DPSK Pools")
class CreateDpskPoolsPhase(PhaseExecutor):

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        dpsk_pool_ids: Dict[str, str] = {}   # pool_key -> R1 pool id
        pools_created: int = 0
        pools_linked: int = 0
        skipped: bool = False

    async def execute(self, inputs: Inputs) -> Outputs:
        pools: Dict[str, Dict[str, Any]] = self.context.options.get("dpsk_pools") or {}
        assignments: Dict[str, str] = self.context.options.get("dpsk_assignments") or {}
        used = {k for k in assignments.values() if k in pools}
        if not used:
            return self.Outputs(skipped=True)

        pool_ids: Dict[str, str] = {}
        created = linked = 0

        for key in sorted(used):
            spec = pools[key]
            if spec.get("mode") == "existing":
                pool_ids[key] = spec["existing_pool_id"]
                linked += 1
                await self.emit(f"DPSK service '{key}' → existing R1 pool "
                                f"(id={spec['existing_pool_id'][:12]})")
                continue

            name = spec["name"]
            ig = spec.get("identity_group") or {}
            if ig.get("mode") == "existing":
                ig_id = ig["id"]
            else:
                await self.emit(f"Creating identity group '{ig['name']}'...")
                result = await self.r1_client.identity.create_identity_group(
                    name=ig["name"],
                    description=f"Migrated from SZ — DPSK service '{name}'",
                    tenant_id=self.tenant_id,
                )
                ig_id = (result or {}).get("id")
                if not ig_id:
                    raise RuntimeError(f"Identity group creation returned no ID for '{ig['name']}'")
                await self.track_resource("identity_groups", {"id": ig_id, "name": ig["name"]})

            await self.emit(f"Creating DPSK pool '{name}'...")
            pool = await self.r1_client.dpsk.create_dpsk_pool(
                identity_group_id=ig_id,
                name=name,
                tenant_id=self.tenant_id,
                description="Migrated from SZ",
                passphrase_length=spec["passphrase_length"],
                passphrase_format=spec["passphrase_format"],
                # 0 = unlimited: create_dpsk_pool then omits deviceCountLimit.
                max_devices_per_passphrase=spec.get("devices_per_passphrase") or 0,
                expiration_days=spec.get("expiration_days"),
                # Sent only for DICTIONARY_WORDS by create_dpsk_pool; required there.
                word_count=spec.get("word_count"),
                numeric_suffix_enabled=spec.get("numeric_suffix_enabled"),
            )
            pool_id = await self._resolve_pool_id(pool, name)
            pool_ids[key] = pool_id
            created += 1
            await self.track_resource("dpsk_pools", {"id": pool_id, "name": name})
            await self.emit(f"Created DPSK pool '{name}' (id={pool_id[:12]})", "success")

        await self.emit(f"DPSK pools: {created} created, {linked} existing", "success")
        return self.Outputs(dpsk_pool_ids=pool_ids, pools_created=created, pools_linked=linked)

    async def _resolve_pool_id(self, response: Dict[str, Any], name: str) -> str:
        """Pool creation is async (202 + requestId); wait, then find it by name."""
        if response and response.get("id"):
            if response.get("requestId"):
                await self.r1_client.await_task_completion(
                    request_id=response["requestId"], override_tenant_id=self.tenant_id,
                )
            return response["id"]
        if response and response.get("requestId"):
            await self.r1_client.await_task_completion(
                request_id=response["requestId"], override_tenant_id=self.tenant_id,
            )
        found = await self.r1_client.dpsk.query_dpsk_pools(
            tenant_id=self.tenant_id, search_string=name, page=1, limit=100,
        )
        for p in (found or {}).get("data", []):
            if p.get("name") == name and p.get("id"):
                return p["id"]
        raise RuntimeError(f"DPSK pool '{name}' was created but could not be found")
