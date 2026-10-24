#!/usr/bin/env python3
"""
Job-state write contention under a big job.

A 259-unit SZ migration lost 2 units with "gave up updating job state after
50 conflicting writes": every per-unit phase that tracks a created resource
rewrites the job blob under WATCH, and 50 attempts with millisecond backoff
(~3s) ran out. The networks were created in R1; the units still failed.

WHAT THIS GUARDS

  1. 300 concurrent writers on one job whose blob already carries 600
     tracked resources all land -- none gives up, none is lost.
  2. The retry is bounded by time, so a writer that truly can't get through
     still fails (with the attempt count), instead of spinning forever.

Uses the real Redis (a throwaway job key, deleted afterwards).

Usage:
    docker compose exec backend python scripts/test_job_state_contention.py
"""

import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import workflow.v2.state_manager as sm_mod
from redis_client import get_redis_client
from workflow.v2.models import WorkflowJobV2, JobStatus
from workflow.v2.state_manager import RedisStateManagerV2, PREFIX

WRITERS = 300


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def main() -> int:
    failures = 0
    redis = await get_redis_client()
    sm = RedisStateManagerV2(redis)
    job_id = f"test-contention-{uuid.uuid4()}"
    key = f"{PREFIX}:jobs:{job_id}"
    job = WorkflowJobV2(id=job_id, workflow_name="test", status=JobStatus.RUNNING,
                        venue_id="v", tenant_id="t", controller_id=0, user_id=0)
    job.created_resources = {"wifi_networks": [{"id": f"n{i}", "name": f"net-{i}" * 3} for i in range(600)]}
    await redis.setex(key, 600, job.model_dump_json(exclude={"units"}))
    try:
        print(f"\n1. {WRITERS} concurrent writers")
        t0 = time.time()
        results = await asyncio.gather(
            *(sm.append_job_error(job_id, f"e{i}") for i in range(WRITERS)), return_exceptions=True)
        took = time.time() - t0
        errs = [r for r in results if isinstance(r, Exception)]
        failures += check("no writer gave up", not errs, f"{len(errs)} failed: {errs[:1]}")
        saved = WorkflowJobV2.model_validate_json(await redis.get(key))
        failures += check("every write landed", sorted(saved.errors) == sorted(f"e{i}" for i in range(WRITERS)),
                          f"{len(saved.errors)}/{WRITERS} in {took:.1f}s")
        failures += check("existing tracked resources untouched",
                          len(saved.created_resources["wifi_networks"]) == 600)

        print("\n2. Bounded by time")
        old = sm_mod.MUTATE_BUDGET_SECONDS
        sm_mod.MUTATE_BUDGET_SECONDS = 0.3
        stop = asyncio.Event()

        payload = await redis.get(key)

        async def hammer():
            # Rewrites the key continuously so a WATCH never holds.
            while not stop.is_set():
                await redis.setex(key, 600, payload)

        hammers = [asyncio.create_task(hammer()) for _ in range(16)]
        await asyncio.sleep(0.05)   # rewriting already, before the writer starts
        t0 = time.time()
        try:
            await sm.append_job_error(job_id, "never")
            failures += check("a writer that can't get through fails", False, "it succeeded")
        except RuntimeError as e:
            failures += check("a writer that can't get through fails, saying how many attempts",
                              "gave up updating job state after" in str(e) and time.time() - t0 < 5, str(e))
        finally:
            stop.set()
            await asyncio.gather(*hammers)
            sm_mod.MUTATE_BUDGET_SECONDS = old
    finally:
        await redis.delete(key)
        await redis.srem(f"{PREFIX}:jobs:active", job_id)

    print(f"\n{'FAILED' if failures else 'All checks passed'}{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
