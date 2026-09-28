#!/usr/bin/env python3
"""
End-to-end scheduler test for a shared global phase under per-unit load.

Drives the real WorkflowBrain.execute_workflow loop against the real Redis,
on the shape of a per_unit Cloudpath import:

    validate (global, done)
      ├── create_shared    (global, critical)   one identity group + pool
      │     └── create_passphrases (per-unit)   needs identity_group_id
      │            └── audit (global)
      └── create_ap_group  (per-unit)           tracks a resource each

Only the phase bodies are fakes. Scheduling, state writes, input wiring and
final status are the production code.

  1. create_shared succeeds while 80 create_ap_group units track resources
     concurrently. The job must complete: create_shared must stay COMPLETED
     and its identity_group_id must reach every unit. Before job-blob writes
     were atomic, a unit's stale save could put create_shared back to
     RUNNING and the job stalled ("ready but never scheduled").
  2. create_shared fails. The job must end FAILED promptly with the phase's
     real error, not a stall message 30s later, and without cancelling the
     create_ap_group tasks already in flight -- cancelling one mid-R1-call
     can leave a resource created and never tracked.

Uses throwaway jobs that are deleted afterwards.

Usage:
    docker compose exec backend python scripts/test_brain_shared_phase_e2e.py

Exits non-zero on failure.
"""

import asyncio
import random
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import BaseModel, ConfigDict

from redis_client import get_redis_client
from workflow.v2.brain import WorkflowBrain, STALL_GRACE_SECONDS
from workflow.v2.models import (
    JobStatus, PhaseDefinitionV2, PhaseStatus, UnitMapping, WorkflowJobV2,
)
from workflow.v2.state_manager import PREFIX, RedisStateManagerV2

UNITS = 80


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


PHASES = [
    PhaseDefinitionV2(id="validate", name="v", executor="validate", per_unit=False),
    PhaseDefinitionV2(id="create_shared", name="s", executor="create_shared",
                      per_unit=False, critical=True, depends_on=["validate"]),
    PhaseDefinitionV2(id="create_ap_group", name="a", executor="create_ap_group",
                      per_unit=True, critical=True, depends_on=["validate"]),
    PhaseDefinitionV2(id="create_passphrases", name="p", executor="create_passphrases",
                      per_unit=True, critical=True, depends_on=["create_shared"]),
    PhaseDefinitionV2(id="audit", name="au", executor="audit", per_unit=False,
                      critical=False, depends_on=["create_passphrases"]),
]


def make_phases(state, shared_fails, stats):
    class Base:
        class Inputs(BaseModel):
            model_config = ConfigDict(extra="allow")

        def __init__(self, context):
            self.context = context

    class CreateShared(Base):
        async def execute(self, inputs):
            await asyncio.sleep(0.2)
            if shared_fails:
                raise RuntimeError("R1 rejected the DPSK pool (test)")
            return {"identity_group_id": "ig-shared"}

    class CreateApGroup(Base):
        async def execute(self, inputs):
            stats["ap_started"] += 1
            await asyncio.sleep(random.uniform(0.05, 0.6))
            await state.track_created_resource(
                self.context.job_id, "ap_groups", {"unit": self.context.unit_id}
            )
            stats["ap_finished"] += 1
            return {}

    class CreatePassphrases(Base):
        class Inputs(BaseModel):
            identity_group_id: str

        async def execute(self, inputs):
            stats["ig_seen"].add(inputs.identity_group_id)
            return {}

    class Audit(Base):
        async def execute(self, inputs):
            return {}

    return {
        "create_shared": CreateShared, "create_ap_group": CreateApGroup,
        "create_passphrases": CreatePassphrases, "audit": Audit,
    }


async def run(state, shared_fails):
    stats = {"ap_started": 0, "ap_finished": 0, "ig_seen": set()}
    job = WorkflowJobV2(
        id=f"test-e2e-{uuid.uuid4()}", workflow_name="test",
        phase_definitions=PHASES,
        global_phase_status={"validate": PhaseStatus.COMPLETED},
        units={
            f"u{i}": UnitMapping(unit_id=f"u{i}", unit_number=str(i))
            for i in range(UNITS)
        },
    )
    await state.save_job(job)
    await state.save_all_units(job.id, job.units)

    brain = WorkflowBrain(state, activity_tracker=None)
    classes = make_phases(state, shared_fails, stats)

    async def noop(*a, **k):
        return None

    brain._reconcile_venue_wide_limit = noop
    brain._pre_complete_resolved_phases = noop
    brain._resolve_phase_class = lambda pid, pdef=None: classes[pid]

    t0 = time.time()
    try:
        done = await asyncio.wait_for(brain.execute_workflow(job), timeout=120)
        stored = await state.get_job_metadata(job.id)
        return done, stored, stats, time.time() - t0
    finally:
        await state.delete_job(job.id)
        await state.redis.srem(f"{PREFIX}:jobs:active", job.id)


async def main() -> int:
    state = RedisStateManagerV2(await get_redis_client())
    failures = 0

    print(f"1. shared phase succeeds under {UNITS} concurrent writers\n")
    done, stored, stats, secs = await run(state, shared_fails=False)
    failures += check("job completes", done.status == JobStatus.COMPLETED,
                      f"{done.status.value}: {done.errors[-2:]}")
    failures += check(
        "create_shared stays COMPLETED in Redis",
        stored.global_phase_status.get("create_shared") == PhaseStatus.COMPLETED,
        str(stored.global_phase_status.get("create_shared")),
    )
    failures += check("its identity_group_id reached the units",
                      stats["ig_seen"] == {"ig-shared"}, str(stats["ig_seen"]))
    failures += check(
        "every unit's tracked resource survives",
        len(stored.created_resources.get("ap_groups", [])) == UNITS,
        f"{len(stored.created_resources.get('ap_groups', []))}/{UNITS}",
    )

    print("\n2. shared phase fails while units are in flight\n")
    done, stored, stats, secs = await run(state, shared_fails=True)
    failures += check("job ends FAILED", done.status == JobStatus.FAILED, done.status.value)
    failures += check(
        "the real error is recorded, not just a stall",
        any("R1 rejected the DPSK pool" in e for e in done.errors)
        and not any("stalled" in e for e in done.errors),
        str(done.errors),
    )
    failures += check(
        f"ends promptly, not after the {STALL_GRACE_SECONDS}s stall grace",
        secs < STALL_GRACE_SECONDS, f"{secs:.1f}s",
    )
    failures += check(
        "in-flight units were drained, not cancelled",
        stats["ap_started"] == stats["ap_finished"]
        and len(stored.created_resources.get("ap_groups", [])) == stats["ap_finished"],
        f"started={stats['ap_started']} finished={stats['ap_finished']} "
        f"tracked={len(stored.created_resources.get('ap_groups', []))}",
    )
    failures += check("no passphrase work ran", not stats["ig_seen"], str(stats["ig_seen"]))

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
