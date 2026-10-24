#!/usr/bin/env python3
"""
SZ -> R1 migration: creating hundreds of AP Groups (per-unit sites).

A 358-group run created 5 groups and failed the whole job: create_ap_group
looked each new group up by name after R1's task finished, R1's query index
hadn't caught up for the 6th, "no id returned" raised, and create_ap_groups
-- critical -- stopped networks, DPSK and activations for all 365 WLANs.

WHAT THIS GUARDS
  1. create_ap_group returns the id R1 sends with the request; no lookup.
  2. Without one, the name lookup is retried while R1's index catches up.
  3. create_ap_groups reads the venue's groups once, reuses existing ones,
     creates the rest a few at a time, and one refusal doesn't stop the
     others or raise -- it's published as failed for the run's outcome.

Usage:
    docker compose exec backend python scripts/test_create_ap_groups.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import r1api.services.venues as venues_mod
from r1api.services.venues import VenueService
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.create_ap_groups as phase_mod


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


class Resp:
    def __init__(self, code, body):
        self.status_code, self._body, self.content = code, body, b"x"

    def json(self):
        return self._body


class FakeClient:
    ec_type = "MSP"

    def __init__(self, with_id=True):
        self.with_id = with_id
        self.posts = 0

    def post(self, path, payload=None, override_tenant_id=None):
        self.posts += 1
        body = {"requestId": "r1"}
        if self.with_id:
            body["response"] = {"id": "new-id"}
        return Resp(202, body)

    async def await_task_completion(self, request_id, override_tenant_id=None):
        return {"status": "SUCCESS"}


async def no_sleep(_):
    return None


def test_create_ap_group():
    print("\n1-2. create_ap_group")
    f = 0
    svc = VenueService(FakeClient(with_id=True))
    lookups = []

    async def find(t, v, n):
        lookups.append(n)
        return None
    svc.find_ap_group_by_name = find
    g = asyncio.run(svc.create_ap_group("t", "v", "10106"))
    f += check("id from R1's response, no lookup", g.get("id") == "new-id" and not lookups, str((g, lookups)))

    svc = VenueService(FakeClient(with_id=False))
    calls = []

    async def lagging(t, v, n):
        calls.append(n)
        return {"id": "found-id", "name": n} if len(calls) >= 3 else None
    svc.find_ap_group_by_name = lagging
    real_sleep = venues_mod.asyncio.sleep          # the asyncio module itself -- put it back
    venues_mod.asyncio.sleep = no_sleep
    try:
        g = asyncio.run(svc.create_ap_group("t", "v", "10107"))
    finally:
        venues_mod.asyncio.sleep = real_sleep
    f += check("no id in the response: lookup retried until R1's index has it",
               g.get("id") == "found-id" and len(calls) == 3, str((g, calls)))
    return f


def test_phase():
    print("\n3. create_ap_groups phase")
    f = 0
    names = [str(10100 + i) for i in range(1, 21)] + ["7107"]
    job = SimpleNamespace(global_phase_results={"sz_validate_and_plan": {"ap_groups_to_create": names}})
    created, in_flight, peak = [], [0], [0]

    async def list_groups(t, v):
        return [{"id": "g-7107", "name": "7107"}]

    async def create(tenant_id, venue_id, name, description=None):
        in_flight[0] += 1
        peak[0] = max(peak[0], in_flight[0])
        await asyncio.sleep(0.01)
        in_flight[0] -= 1
        if name == "10106":
            raise RuntimeError("R1 said no")
        created.append(name)
        return {"id": f"g-{name}"}

    r1 = SimpleNamespace(venues=SimpleNamespace(list_ap_groups_in_venue=list_groups, create_ap_group=create))

    async def get_job(_):
        return job
    p = phase_mod.CreateApGroupsPhase(PhaseContext(
        job_id="j", r1_client=r1, venue_id="v", tenant_id="t", options={},
        state_manager=SimpleNamespace(get_job=get_job)))

    async def emit(*a, **k):
        return None

    async def track(*a, **k):
        return None
    p.emit, p.track_resource = emit, track
    out = asyncio.run(p.execute(p.Inputs(sz_snapshot_job_id="s")))
    f += check("existing group reused, not created again", out.ap_group_ids.get("7107") == "g-7107" and "7107" not in created)
    f += check("one refusal doesn't stop the rest (19 of 20 created) and doesn't raise",
               out.created == 19 and list(out.failed) == ["10106"] and "R1 said no" in out.failed["10106"], str(out.failed))
    f += check("created a few at a time", 1 < peak[0] <= phase_mod.CREATE_CONCURRENCY, str(peak[0]))
    return f


if __name__ == "__main__":
    failures = test_create_ap_group() + test_phase()
    print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
    sys.exit(1 if failures else 0)
