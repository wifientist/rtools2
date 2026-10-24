"""
SZ → R1 Migration: Create AP Groups (Global)

Creates every R1 AP Group the plan needs: the ones ticked from the SZ
broadcast, and the per-unit (staged) ones. They are created empty -- APs
join them in AP Mapping -- so that activations have a target.

Publishes R1 name -> id for activate_ssids. Global (once per job) because
several WLANs activate onto the same group.

The venue's groups are read once; the missing ones are created a few at a
time (a per-unit site needs hundreds). One group R1 refuses doesn't stop
the rest or the job: networks, DPSK and activations still run (a WLAN aimed
at a group that wasn't created fails on its own, saying so), and the
failures are published for the run's outcome.
"""

import asyncio
import logging
from typing import Dict

from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)

CREATE_CONCURRENCY = 5


@register_phase("create_ap_groups", "Create AP Groups")
class CreateApGroupsPhase(PhaseExecutor):

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        ap_group_ids: Dict[str, str] = {}   # R1 name -> id, for groups this run created or found
        created: int = 0
        failed: Dict[str, str] = {}          # name -> R1's error
        skipped: bool = False

    async def execute(self, inputs: Inputs) -> Outputs:
        job = await self.context.state_manager.get_job(self.job_id)
        names = (job.global_phase_results.get("sz_validate_and_plan") or {}).get("ap_groups_to_create") or []
        if not names:
            return self.Outputs(skipped=True)

        # Re-run safety, once for all: a previous attempt may have created some.
        existing = {
            (g.get("name") or "").strip().casefold(): g.get("id")
            for g in await self.r1_client.venues.list_ap_groups_in_venue(self.tenant_id, self.venue_id)
            if g.get("id")
        }
        ids: Dict[str, str] = {}
        todo = []
        for name in names:
            gid = existing.get(name.strip().casefold())
            if gid:
                ids[name] = gid
            else:
                todo.append(name)
        if ids:
            await self.emit(f"{len(ids)} AP Group(s) already exist — reused")
        if not todo:
            return self.Outputs(ap_group_ids=ids)

        await self.emit(f"Creating {len(todo)} AP Group(s)...")
        sem = asyncio.Semaphore(CREATE_CONCURRENCY)
        failed: Dict[str, str] = {}
        created = 0

        async def one(name: str):
            nonlocal created
            async with sem:
                try:
                    group = await self.r1_client.venues.create_ap_group(
                        tenant_id=self.tenant_id, venue_id=self.venue_id, name=name,
                    )
                    group_id = (group or {}).get("id")
                    if not group_id:
                        raise RuntimeError("R1 accepted it but returned no id")
                    ids[name] = group_id
                    created += 1
                    await self.track_resource("ap_groups", {"id": group_id, "name": name})
                    if created % 25 == 0 or created == len(todo):
                        await self.emit(f"Created {created}/{len(todo)} AP Group(s)")
                except Exception as e:
                    failed[name] = str(e)[:300]
                    await self.emit(f"AP Group '{name}' not created: {e}", "warning")

        await asyncio.gather(*(one(n) for n in todo))
        if failed:
            await self.emit(f"{len(failed)} of {len(todo)} AP Group(s) not created — "
                            "their WLANs can't be activated there; re-run to retry", "warning")
        else:
            await self.emit(f"Created all {created} AP Group(s)", "success")
        return self.Outputs(ap_group_ids=ids, created=created, failed=failed)
