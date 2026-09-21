#!/usr/bin/env python3
"""
Identity-group / DPSK-pool linkage regression test.

WHAT WENT WRONG

create_shared_resources looked up the identity group by name and the DPSK
pool by name, independently, and assumed the two were attached to each other.
They need not be. An identity group belongs to exactly one pool, and R1
validates a new passphrase against THAT pool -- not against whichever pool
happens to share the import's name.

So a property whose group survived an earlier run but whose pool link did
not produced a run that logged:

    Reusing identity group: Southgate Apartments
    Reusing DPSK pool:      Southgate Apartments

and then failed every single passphrase with

    GENERAL-010  Invalid Identity: The group it belongs to has no pool
                 associated with it

Confirmed on the live tenant: the group existed with dpskPoolId=None while a
pool of the same name existed unattached.

It stayed hidden until create_passphrase was fixed (the shadowed asyncio
import), because before that every passphrase died locally before R1 saw it.

WHAT THIS GUARDS

  1. A reused group with NO pool gets the pool attached before use.
  2. A reused group already attached to that pool is left alone.
  3. A group attached to a DIFFERENT pool wins -- that is the pool R1
     validates against -- and the mismatch is reported.
  4. A failed attach raises rather than starting a run that cannot create a
     single passphrase.
  5. A newly created group still gets its pool created FROM the group.

Usage:
    docker compose exec backend python scripts/test_shared_resource_linking.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.phases.cloudpath.shared_resources import CreateSharedResourcesPhase
from workflow.phases.cloudpath.validate import CloudpathPoolConfig


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


class FakeIdentity:
    def __init__(self, w):
        self.w = w

    async def query_identity_groups(self, **kw):
        return {"content": self.w["groups"]}

    async def attach_dpsk_pool_to_identity_group(self, group_id, dpsk_pool_id, tenant_id=None):
        if self.w.get("attach_fails"):
            raise RuntimeError("DPSK-10032 already associated with other dpsk service")
        self.w["attached"].append((group_id, dpsk_pool_id))
        for g in self.w["groups"]:
            if g["id"] == group_id:
                g["dpskPoolId"] = dpsk_pool_id
        return {"status": "attached"}

    async def create_identity_group(self, **kw):
        rec = {"id": "ig-new", "name": kw.get("name"), "dpskPoolId": None}
        self.w["groups"].append(rec)
        return rec


class FakeDpsk:
    def __init__(self, w):
        self.w = w

    async def query_dpsk_pools(self, **kw):
        return {"data": self.w["pools"]}

    async def get_dpsk_pool(self, pool_id, tenant_id=None):
        return next((p for p in self.w["pools"] if p["id"] == pool_id), {})

    async def update_dpsk_pool(self, **kw):
        return True


class FakeClient:
    def __init__(self, w):
        self.identity = FakeIdentity(w)
        self.dpsk = FakeDpsk(w)


def make_phase(w, messages):
    phase = CreateSharedResourcesPhase.__new__(CreateSharedResourcesPhase)
    phase.r1_client = FakeClient(w)
    phase.tenant_id = "t-1"
    phase.venue_id = "v-1"

    async def emit(msg, level="info", details=None):
        messages.append((level, msg))

    async def track_resource(kind, data):
        return None

    phase.emit = emit
    phase.track_resource = track_resource

    class Ctx:
        activity_tracker = None
    phase.context = Ctx()
    return phase


async def run(w):
    messages = []
    phase = make_phase(w, messages)
    out = await phase.execute(CreateSharedResourcesPhase.Inputs(
        identity_groups=[{"name": "Southgate Apartments"}],
        dpsk_pools=[{"name": "Southgate Apartments"}],
        pool_config=CloudpathPoolConfig(name="Southgate Apartments"),
    ))
    return out, w, " ".join(m for _, m in messages)


def world(pool_on_group=None, pools=True):
    return {
        "groups": [{"id": "ig-1", "name": "Southgate Apartments",
                    "dpskPoolId": pool_on_group}],
        "pools": ([{"id": "pool-1", "name": "Southgate Apartments"}] if pools else []),
        "attached": [],
    }


async def main() -> int:
    failures = 0
    print("Identity group / DPSK pool linkage\n")

    # 1. the reported failure: group with no pool
    out, w, text = await run(world(pool_on_group=None))
    failures += check(
        "a reused group with NO pool gets the pool attached",
        w["attached"] == [("ig-1", "pool-1")]
        and out.dpsk_pool_id == "pool-1"
        and "had none" in text,
        f"attached={w['attached']}, pool={out.dpsk_pool_id}",
    )

    # 2. already linked -> no write
    out, w, _ = await run(world(pool_on_group="pool-1"))
    failures += check(
        "a group already attached to that pool is left alone",
        w["attached"] == [] and out.dpsk_pool_id == "pool-1",
        f"attached={w['attached']}",
    )

    # 3. attached to a different pool -> the group wins, and says so
    out, w, text = await run(world(pool_on_group="pool-OTHER"))
    failures += check(
        "a group attached to a DIFFERENT pool wins, with a warning",
        out.dpsk_pool_id == "pool-OTHER" and w["attached"] == []
        and "different" in text.lower(),
        f"pool={out.dpsk_pool_id}, attached={w['attached']}",
    )

    # 4. a failed attach must stop the run
    w4 = world(pool_on_group=None)
    w4["attach_fails"] = True
    try:
        await run(w4)
        failures += check("a failed attach stops the run", False, "no exception")
    except RuntimeError as e:
        failures += check(
            "a failed attach stops the run rather than starting a doomed one",
            "no DPSK pool" in str(e) and "stopping here" in str(e),
            str(e)[:90],
        )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
