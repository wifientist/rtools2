#!/usr/bin/env python3
"""
Identity-cache and non-blocking-R1 regression test.

Both guards come from one production incident (2026-09-16). An import into a
property whose identities already existed produced a GENERAL-010 per
passphrase, and each one re-paged the ENTIRE identity group:

    ~24 requests per failure x ~60 failures = ~1,400 requests
    re-reading a list that was not changing

The R1 client is synchronous (requests), and the async service methods called
it directly, so every one of those blocked the event loop. Redis connects for
/jobs/{id}/status then exceeded their 10s timeout and the progress UI 500ed
while the import itself was healthy:

    File "/app/workflow/v2/state_manager.py", line 112, in get_job
      data = await self.redis.get(key)
    redis.exceptions.TimeoutError: Timeout connecting to server

WHAT THIS GUARDS

  1. N GENERAL-010 recoveries page the identity group ONCE, not N times.
  2. The recovery still works: each passphrase is retried against the
     identity id resolved from that one read.
  3. A name missing from the cached view triggers at most one refresh, shared
     by every caller waiting, rather than one sweep each.
  4. The hot R1 calls do not block the event loop, so slow R1 responses can
     no longer starve Redis, SSE or the job heartbeat.

Usage:
    docker compose exec backend python scripts/test_r1_nonblocking.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from r1api.services.identity import IdentityService
from workflow.phases.cloudpath.passphrases import CreatePassphrasesPhase

GROUP_ID = "grp-1"
POOL_ID = "pool-1"
UNITS = 12


class FakeDpsk:
    """Rejects a username the way R1 does when its identity already exists."""

    def __init__(self, calls):
        self.calls = calls

    async def create_passphrase(self, pool_id, passphrase, tenant_id=None,
                                user_name=None, identity_id=None, **kw):
        self.calls.append("create_passphrase")
        if identity_id:
            return {"id": f"pp-{identity_id}", "identityId": identity_id}
        raise RuntimeError(
            "GENERAL-010: Invalid Identity: An identity with this name "
            "already exists in the group."
        )


class FakeIdentity:
    def __init__(self, calls, names):
        self.calls, self.names = calls, names

    async def get_identities_in_group(self, group_id, tenant_id=None, page=0, size=100):
        self.calls.append("get_identities_in_group")
        if page:
            return {"content": []}
        return {"content": [{"id": f"id-{n}", "name": n} for n in self.names]}


class FakeR1:
    def __init__(self, calls, names):
        self.dpsk = FakeDpsk(calls)
        self.identity = FakeIdentity(calls, names)


def make_phase(calls, names):
    phase = CreatePassphrasesPhase.__new__(CreatePassphrasesPhase)
    phase.r1_client = FakeR1(calls, names)
    phase.tenant_id = "t-1"
    phase.venue_id = "v-1"
    phase.job_id = "job-1"
    phase.context = None

    async def noop(*a, **kw):
        return None

    phase.emit = noop
    phase.emit_progress = noop
    phase.track_resource = noop
    return phase


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def main() -> int:
    failures = 0
    print("Identity cache and non-blocking R1\n")

    names = [f"90000{i}_gigabit" for i in range(UNITS)]
    calls = []
    phase = make_phase(calls, names)
    inputs = CreatePassphrasesPhase.Inputs(
        passphrases=[
            {"guid": f"g-{n}", "name": n, "passphrase": f"pw-{n}", "status": "ACTIVE"}
            for n in names
        ],
        dpsk_pool_id=POOL_ID,
        identity_group_id=GROUP_ID,
        options={},
    )
    out = await phase.execute(inputs)

    sweeps = calls.count("get_identities_in_group")
    failures += check(
        f"{UNITS} GENERAL-010 recoveries page the group once, not {UNITS} times",
        sweeps == 1, f"pages read = {sweeps}",
    )
    failures += check(
        "every passphrase still recovered onto its existing identity",
        out.created_count == UNITS and out.failed_count == 0,
        f"created={out.created_count} failed={out.failed_count}",
    )

    # A name absent from the cached view: one shared refresh, not one each.
    calls2 = []
    phase2 = make_phase(calls2, [])          # group reports nothing
    phase2.r1_client.identity.names = []
    out2 = await phase2.execute(inputs)
    sweeps2 = calls2.count("get_identities_in_group")
    failures += check(
        "an unknown name refreshes at most twice in total, not per passphrase",
        sweeps2 <= 2, f"pages read = {sweeps2}",
    )

    # The hot R1 call must not hold the event loop.
    class SlowClient:
        ec_type = "EC"

        def get(self, *a, **kw):
            time.sleep(0.25)          # a blocking R1 round trip
            return object()

        def safe_json(self, response):
            return {"content": []}

    svc = IdentityService(SlowClient())
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.02)
            ticks += 1

    started = time.monotonic()
    await asyncio.gather(
        svc.get_identities_in_group(GROUP_ID),
        svc.get_identities_in_group(GROUP_ID),
        heartbeat(),
    )
    elapsed = time.monotonic() - started
    failures += check(
        "two slow R1 calls run concurrently, and the loop keeps ticking",
        elapsed < 0.45 and ticks >= 15,
        f"elapsed={elapsed:.2f}s (serial would be ~0.50s), loop ticks={ticks}/20",
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
