"""
SZ → R1 Migration: Apply Venue Settings (Global)

Carries the zone's settings onto the destination venue -- channel plans and
radio behaviour per band, management VLAN, mesh, load/band balancing,
client admission control, AP behaviours, per-AP-model PoE / LAN ports /
LEDs -- the sections chosen in the Venue Configuration stage (all of them by
default: a migration takes the config from SZ).

Each section is read from R1, overlaid with the zone's values and written
back (venue_create.apply_settings), so whatever the zone doesn't set keeps
R1's value. Sections are independent; any that R1 refuses fail the phase
with R1's message, but the phase is not critical -- the networks still
migrate.

Part of the Venue Configuration stage (sz_venue_config workflow), followed
by verify_venue_settings.
"""

import logging
from typing import Any, Dict, List

from pydantic import BaseModel

from services.sz_migration.venue_create import apply_settings
from workflow.phases.sz_migration.venue_config import load_venue_plan, save_stage_report
from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)


@register_phase("apply_venue_settings", "Apply Venue Settings")
class ApplyVenueSettingsPhase(PhaseExecutor):

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        results: List[Dict[str, Any]] = []
        applied: int = 0
        skipped: bool = False

    async def execute(self, inputs: Inputs) -> Outputs:
        # None = every section (the default); [] = none chosen.
        plan, keys, _ = await load_venue_plan(inputs.sz_snapshot_job_id, self.context.options.get("venue_settings"))
        if not keys:
            await self.emit("No venue settings chosen — R1 venue settings left as they are")
            return self.Outputs(skipped=True)

        await self.emit(f"Applying {len(keys)} zone setting section(s) to the venue...")
        results = await apply_settings(self.r1_client, self.tenant_id, self.venue_id, plan, keys)
        await save_stage_report(self.job_id, "apply", results)
        failed = [r for r in results if r["status"] != "applied"]
        for r in results:
            if r["status"] == "applied":
                await self.emit(f"{r['label']}: applied", "success")
                for n in r.get("notes") or []:
                    await self.emit(f"{r['label']}: {n}", "warning")
            else:
                await self.emit(f"{r['label']}: {r.get('error')}", "warning")
        if failed:
            raise RuntimeError(
                f"{len(failed)} of {len(results)} venue setting section(s) not applied: "
                + "; ".join(f"{r['label']} — {r.get('error')}" for r in failed)
            )
        return self.Outputs(results=results, applied=len(results))
