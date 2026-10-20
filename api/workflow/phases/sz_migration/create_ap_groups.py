"""
SZ → R1 Migration: Create AP Groups (Global)

Creates the R1 AP Groups the plan needs: SZ AP Groups that have APs but no
R1 group of the same name. They are created empty -- APs usually reach the
venue after the config migration -- so that activations have a target, and
an AP moved in later picks up the right SSIDs by joining its group.

Publishes R1 name -> id for activate_ssids. Global (once per job) because
several WLANs activate onto the same group.
"""

import logging
from typing import Dict

from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)


@register_phase("create_ap_groups", "Create AP Groups")
class CreateApGroupsPhase(PhaseExecutor):

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        ap_group_ids: Dict[str, str] = {}   # R1 name -> id, for groups this run created or found
        created: int = 0
        skipped: bool = False

    async def execute(self, inputs: Inputs) -> Outputs:
        job = await self.context.state_manager.get_job(self.job_id)
        names = (job.global_phase_results.get("sz_validate_and_plan") or {}).get("ap_groups_to_create") or []
        if not names:
            return self.Outputs(skipped=True)

        ids: Dict[str, str] = {}
        created = 0
        for name in names:
            # Re-run safety: a previous attempt may have created it already.
            existing = await self.r1_client.venues.find_ap_group_by_name(
                self.tenant_id, self.venue_id, name,
            )
            if existing and existing.get("id"):
                ids[name] = existing["id"]
                await self.emit(f"AP Group '{name}' already exists (id={existing['id'][:12]})")
                continue

            await self.emit(f"Creating AP Group '{name}'...")
            group = await self.r1_client.venues.create_ap_group(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                name=name,
                description="Migrated from SZ",
            )
            group_id = (group or {}).get("id")
            if not group_id:
                raise RuntimeError(f"AP Group '{name}' was not created (no id returned)")
            ids[name] = group_id
            created += 1
            await self.track_resource("ap_groups", {"id": group_id, "name": name})
            await self.emit(f"Created AP Group '{name}' (id={group_id[:12]})", "success")

        return self.Outputs(ap_group_ids=ids, created=created)
