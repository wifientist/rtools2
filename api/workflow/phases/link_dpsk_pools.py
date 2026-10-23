"""
V2 Phase: Link DPSK pools (WLAN Activator)

A DPSK network with no DPSK service linked cannot be activated (R1 answers
WIFI-20300). Creating the network and linking its pool are two calls, so an
import that died or raced between them leaves exactly that. When the user
chose a pool for such networks on the WLAN Activator page, validation plans
the links (dpsk_links_to_make: network id -> pool id) and this phase makes
them, once per network, before any AP Group or activation work.

A network that is linked by now (someone fixed it, or a re-run) is left as
it is. A link that fails is reported; that network's activation then fails
on its own with R1's WIFI-20300, the rest of the job carries on.
"""

import asyncio
import logging
from typing import Dict, List

from pydantic import BaseModel, Field

from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.registry import register_phase

logger = logging.getLogger(__name__)


def _linked(resp) -> List[str]:
    rows = resp.get("data") if isinstance(resp, dict) else resp
    return [r.get("id") for r in rows or [] if isinstance(r, dict) and r.get("id")]


@register_phase("link_dpsk_pools", "Link DPSK Pools")
class LinkDpskPoolsPhase(PhaseExecutor):
    """Link the chosen DPSK pool to each DPSK network that has none."""

    class Inputs(BaseModel):
        dpsk_links_to_make: Dict[str, str] = Field(default_factory=dict)   # network id -> pool id
        dpsk_link_names: Dict[str, str] = Field(default_factory=dict)      # network id -> name

    class Outputs(BaseModel):
        dpsk_links_made: List[str] = Field(default_factory=list)
        dpsk_links_failed: Dict[str, str] = Field(default_factory=dict)
        skipped: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        if not inputs.dpsk_links_to_make:
            return self.Outputs(skipped=True)
        await self.emit(f"Linking a DPSK pool to {len(inputs.dpsk_links_to_make)} DPSK network(s)...")
        made: List[str] = []
        failed: Dict[str, str] = {}
        sem = asyncio.Semaphore(3)

        async def link(network_id: str, pool_id: str):
            name = inputs.dpsk_link_names.get(network_id) or network_id
            async with sem:
                try:
                    current = _linked(await self.r1_client.networks.get_dpsk_services_on_network(
                        network_id, self.tenant_id))
                    if current:
                        await self.emit(f"[{name}] Already has a DPSK pool linked — left as it is")
                        return
                    await self.r1_client.networks.activate_dpsk_service_on_network(
                        tenant_id=self.tenant_id, network_id=network_id, dpsk_service_id=pool_id)
                    made.append(network_id)
                    await self.emit(f"[{name}] Linked DPSK pool", "success")
                except Exception as e:
                    failed[network_id] = str(e)
                    await self.emit(f"[{name}] DPSK pool not linked: {e} — its activation will fail", "error")

        await asyncio.gather(*[link(n, p) for n, p in inputs.dpsk_links_to_make.items()])
        await self.emit(f"DPSK pools: {len(made)} linked"
                        + (f", {len(failed)} failed" if failed else ""), "warning" if failed else "success")
        return self.Outputs(dpsk_links_made=made, dpsk_links_failed=failed)

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=bool(inputs.dpsk_links_to_make),
                               estimated_api_calls=2 * len(inputs.dpsk_links_to_make))
