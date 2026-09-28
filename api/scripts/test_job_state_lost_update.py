#!/usr/bin/env python3
"""
Job-state lost-update regression test.

The job blob (global_phase_status, global_phase_results, created_resources,
errors) used to be updated with a plain get_job() + save_job(). Concurrent
writers each saved their own stale copy, and the last save won.

On a per_unit Cloudpath import that is the normal case: create_ap_group runs
for every unit while create_shared_resources is finishing, and each unit
calls track_created_resource. One of them read the blob while the phase was
RUNNING and wrote it back after the phase wrote COMPLETED, so the phase
stayed RUNNING with its outputs gone. Every unit waiting on it stalled, and
the job failed with:

    global 'create_shared_resources' ready but never scheduled;
    80 unit(s): unit phase 'create_passphrases' waiting on
    ['create_shared_resources']

This reproduces that write pattern against the real Redis, using a
throwaway job that is deleted afterwards.

Usage:
    docker compose exec backend python scripts/test_job_state_lost_update.py

Exits non-zero on failure.
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from redis_client import get_redis_client
from workflow.v2.models import PhaseStatus, UnitMapping, WorkflowJobV2
from workflow.v2.state_manager import RedisStateManagerV2

UNITS = 80
ROUNDS = 5


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def one_round(state: RedisStateManagerV2) -> tuple:
    # Not RUNNING, so the throwaway job never shows up as an active job.
    job = WorkflowJobV2(id=f"test-lost-update-{uuid.uuid4()}", workflow_name="test")
    units = {
        f"u{i}": UnitMapping(unit_id=f"u{i}", unit_number=str(i))
        for i in range(UNITS)
    }
    await state.save_job(job)
    await state.save_all_units(job.id, units)
    try:
        await state.update_global_phase_status(
            job.id, "create_shared_resources", PhaseStatus.RUNNING
        )

        async def unit_tracks(i):
            await state.track_created_resource(job.id, "ap_groups", {"id": f"ag{i}"})

        async def phase_finishes():
            await asyncio.sleep(0.005)
            await state.update_global_phase_status(
                job.id, "create_shared_resources", PhaseStatus.COMPLETED,
                result={"identity_group_id": "ig-1"},
            )

        await asyncio.gather(phase_finishes(), *(unit_tracks(i) for i in range(UNITS)))

        final = await state.get_job_metadata(job.id)
        return (
            final.global_phase_status.get("create_shared_resources"),
            final.global_phase_results.get("create_shared_resources"),
            len(final.created_resources.get("ap_groups", [])),
        )
    finally:
        await state.delete_job(job.id)


async def main() -> int:
    state = RedisStateManagerV2(await get_redis_client())
    print(f"Job-state lost updates ({UNITS} concurrent writers x {ROUNDS} rounds)\n")

    failures = 0
    for r in range(ROUNDS):
        status, results, tracked = await one_round(state)
        failures += check(
            f"round {r + 1}: phase status survives concurrent writers",
            status == PhaseStatus.COMPLETED, f"ended as {status}",
        )
        failures += check(
            f"round {r + 1}: phase outputs survive",
            results == {"identity_group_id": "ig-1"}, f"ended as {results}",
        )
        failures += check(
            f"round {r + 1}: every tracked resource survives",
            tracked == UNITS, f"{tracked}/{UNITS}",
        )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
