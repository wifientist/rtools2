"""
SZ → R1 Migration: Phase 3 — Configure DPSK (Per-WLAN)

For DPSK WLANs, creates the identity group and DPSK pool in R1,
then links the DPSK service to the WiFi network.
Skips non-DPSK WLANs.
"""

import logging
from typing import Optional
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.idempotent import IdempotentHelper

logger = logging.getLogger(__name__)


@register_phase("configure_dpsk", "Configure DPSK")
class ConfigureDPSKPhase(PhaseExecutor):
    """Per-WLAN phase: create identity group + DPSK pool for DPSK networks."""

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_id: Optional[str] = None

    class Outputs(BaseModel):
        dpsk_pool_id: str = ""
        identity_group_id: str = ""
        skipped: bool = False
        reused: bool = False

    async def execute(self, inputs: Inputs) -> Outputs:
        unit = None
        if self.context.state_manager:
            unit = await self.context.state_manager.get_unit(self.job_id, inputs.unit_id)

        plan_extra = unit.plan.extra if unit else {}
        r1_type = plan_extra.get("r1_network_type", "")
        wlan_name = plan_extra.get("sz_wlan_name", inputs.unit_number)

        # Skip non-DPSK WLANs
        if r1_type != "dpsk":
            return self.Outputs(skipped=True)

        if not inputs.network_id:
            await self.emit(f"[{wlan_name}] No network_id — skipping DPSK config", "warning")
            return self.Outputs(skipped=True)

        # DPSK service chosen on Review: create_dpsk_pools has already made
        # (or found) it, so this unit only links it to its network.
        pool_key = plan_extra.get("dpsk_pool_key")
        if pool_key:
            job = await self.context.state_manager.get_job(self.job_id)
            pool_ids = job.global_phase_results.get("create_dpsk_pools", {}).get("dpsk_pool_ids", {})
            pool_id = pool_ids.get(pool_key)
            if not pool_id:
                raise RuntimeError(f"[{wlan_name}] DPSK service '{pool_key}' was not created")
            # A re-run: most networks already carry their pool. Read it (one
            # GET) instead of re-sending the link -- the PUT costs an activity
            # and a config push to the APs every time.
            try:
                linked = await self.r1_client.networks.get_dpsk_services_on_network(inputs.network_id, self.tenant_id)
                linked_ids = {d.get("id") for d in (linked or {}).get("data", []) if d.get("id")}
            except Exception as e:
                linked_ids = set()
                logger.debug(f"[{wlan_name}] Could not read linked DPSK services, linking anyway: {e}")
            if pool_id in linked_ids:
                await self.emit(f"[{wlan_name}] DPSK service already linked")
                return self.Outputs(dpsk_pool_id=pool_id)
            await self.emit(f"[{wlan_name}] Linking DPSK service...")
            await self.r1_client.networks.activate_dpsk_service_on_network(
                tenant_id=self.tenant_id,
                network_id=inputs.network_id,
                dpsk_service_id=pool_id,
            )
            await self.emit(f"[{wlan_name}] DPSK service linked (id={pool_id[:12]})", "success")
            return self.Outputs(dpsk_pool_id=pool_id)

        # Older plan (no DPSK choices): per-WLAN {wlan}-pool, as before.
        # Use the existing idempotent helper pattern
        helper = IdempotentHelper(self.r1_client)

        # ── Create/reuse identity group ───────────────────────────────
        ig_name = f"{wlan_name}-identities"
        await self.emit(f"[{wlan_name}] Finding/creating identity group '{ig_name}'...")

        ig_result = await helper.find_or_create_identity_group(
            tenant_id=self.tenant_id,
            name=ig_name,
        )
        ig_id = ig_result.get("id", "")
        if not ig_id:
            raise RuntimeError(f"Identity group creation returned no ID for '{ig_name}'")

        reused_ig = ig_result.get("existed", False)
        await self.emit(
            f"[{wlan_name}] {'Reused' if reused_ig else 'Created'} identity group (id={ig_id[:12]})"
        )
        await self.track_resource("identity_groups", {"id": ig_id, "name": ig_name})

        # ── Create/reuse DPSK pool ────────────────────────────────────
        pool_name = f"{wlan_name}-pool"
        await self.emit(f"[{wlan_name}] Finding/creating DPSK pool '{pool_name}'...")

        pool_result = await helper.find_or_create_dpsk_pool(
            tenant_id=self.tenant_id,
            name=pool_name,
            identity_group_id=ig_id,
        )
        pool_id = pool_result.get("id", "")
        if not pool_id:
            raise RuntimeError(f"DPSK pool creation returned no ID for '{pool_name}'")

        reused_pool = pool_result.get("existed", False)
        await self.emit(
            f"[{wlan_name}] {'Reused' if reused_pool else 'Created'} DPSK pool (id={pool_id[:12]})"
        )
        await self.track_resource("dpsk_pools", {"id": pool_id, "name": pool_name})

        # ── Link DPSK service to network ──────────────────────────────
        await self.emit(f"[{wlan_name}] Linking DPSK service to network...")
        await self.r1_client.networks.activate_dpsk_service_on_network(
            tenant_id=self.tenant_id,
            network_id=inputs.network_id,
            dpsk_service_id=pool_id,
        )
        await self.emit(f"[{wlan_name}] DPSK configured", "success")

        return self.Outputs(
            dpsk_pool_id=pool_id,
            identity_group_id=ig_id,
            reused=reused_ig and reused_pool,
        )
