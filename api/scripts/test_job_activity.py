#!/usr/bin/env python3
"""
The job monitor's activity log and AP Group progress, on real Redis.

    docker compose exec backend python scripts/test_job_activity.py
"""

import asyncio
import json
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, "/app")

from redis_client import get_redis_client  # noqa: E402
from workflow.phases.sz_migration.create_ap_groups import CreateApGroupsPhase  # noqa: E402
from workflow.v2 import state_manager as sm  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{f' -- {detail}' if detail and not ok else ''}")
    if not ok:
        failures.append(label)


class Events:
    def __init__(self):
        self.messages, self.progress = [], []

    async def message(self, job_id, message, level="info", details=None):
        self.messages.append((message, level))

    async def phase_progress(self, **kw):
        self.progress.append(kw)


class Venues:
    def __init__(self, existing=(), refuse=()):
        self.existing, self.refuse = existing, refuse

    async def list_ap_groups_in_venue(self, tenant_id, venue_id):
        return [{"name": n, "id": f"old-{n}"} for n in self.existing]

    async def create_ap_group(self, tenant_id, venue_id, name):
        await asyncio.sleep(0)
        if name in self.refuse:
            raise RuntimeError("R1 refused it")
        return {"id": f"new-{name}"}


async def run_ap_groups(state, names, existing=(), refuse=()):
    job_id = f"test-activity-{uuid.uuid4()}"
    events = Events()

    async def get_job(_):
        return SimpleNamespace(global_phase_results={"sz_validate_and_plan": {"ap_groups_to_create": names}})

    async def track(*a):
        await asyncio.sleep(0)
        return True

    state.get_job, state.track_created_resource = get_job, track
    context = SimpleNamespace(
        job_id=job_id, phase_id="create_ap_groups", unit_id=None, venue_id="v", tenant_id="t",
        options={}, r1_client=SimpleNamespace(venues=Venues(existing, refuse)),
        state_manager=state, event_publisher=events, activity_tracker=None,
        unit_number=None, batch_size=None,
    )
    phase = CreateApGroupsPhase(context)
    outputs = await phase.execute(CreateApGroupsPhase.Inputs(sz_snapshot_job_id="x"))
    log = await state.get_activity(job_id)
    return job_id, outputs, events, log


async def main():
    redis = await get_redis_client()
    state = sm.RedisStateManagerV2(redis)
    keys = []

    print("Log: position survives trimming")
    job = f"test-activity-{uuid.uuid4()}"
    keys += [f"{sm.PREFIX}:jobs:{job}:activity", f"{sm.PREFIX}:jobs:{job}:activity:total"]
    with patch.object(sm, "ACTIVITY_MAX_ROWS", 5):
        for i in range(8):
            await state.append_activity(job, {"name": f"n{i}"})
    page = await state.get_activity(job)
    check("keeps the newest", [r["name"] for r in page["rows"]] == [f"n{i}" for i in range(3, 8)], str(page))
    check("counts what it dropped", page["dropped"] == 3 and page["total"] == 8)
    check("positions are absolute", page["first_position"] == 3 and page["next_position"] == 8)
    later = await state.get_activity(job, position=6)
    check("a client reads on from its position", [r["name"] for r in later["rows"]] == ["n6", "n7"])
    check("nothing new past the end", (await state.get_activity(job, position=8))["rows"] == [])
    old = await state.get_activity(job, position=1)
    check("a position that was trimmed reads what's left", old["first_position"] == 3 and len(old["rows"]) == 5)

    print("AP Groups: progress and rows")
    names = [f"G{i}" for i in range(567)]
    job, out, events, log = await run_ap_groups(state, names)
    keys += [f"{sm.PREFIX}:jobs:{job}:activity", f"{sm.PREFIX}:jobs:{job}:activity:total",
             f"{sm.PREFIX}:jobs:{job}:phase_progress"]
    check("created them all", out.created == 567 and not out.failed)
    counts = [m for m, _ in events.messages if "/567" in m]
    check("no repeated count messages", not counts, str(counts[:5]))
    done = [p["current"] for p in events.progress]
    check("progress reaches the total", done and max(done) == 567 and done[-1] == 567, str(done[-3:]))
    check("each progress update is published once", len(done) == len(set(done)), str(done))
    saved = (await state.get_phase_progress(job))["create_ap_groups"][0]
    check("saved counts are final", (saved["current"], saved["total"]) == (567, 567), str(saved))
    check("one row per group", len(log["rows"]) == 567 and len({r["name"] for r in log["rows"]}) == 567)
    row = log["rows"][0]
    check("row says what happened", row["action"] == "created" and row["type"] == "ap_groups"
          and row["id"].startswith("new-") and row["phase_id"] == "create_ap_groups", str(row))

    job, out, events, log = await run_ap_groups(state, ["A", "B", "C"], existing=["A"], refuse=["B"])
    keys += [f"{sm.PREFIX}:jobs:{job}:activity", f"{sm.PREFIX}:jobs:{job}:activity:total",
             f"{sm.PREFIX}:jobs:{job}:phase_progress"]
    by_name = {r["name"]: r for r in log["rows"]}
    check("reused, failed and created each logged",
          (by_name["A"]["action"], by_name["B"]["action"], by_name["C"]["action"]) == ("reused", "failed", "created"),
          str(by_name))
    check("failure carries R1's reason", "refused" in by_name["B"]["detail"])
    last = events.progress[-1]
    check("final progress counts the failure", (last["current"], last["total"], last["failed"]) == (2, 2, 1), str(last))

    print("Log: only named fields leave a phase")
    job, *_ = await run_ap_groups(state, [])
    context = SimpleNamespace(
        job_id=job, phase_id="p", unit_id=None, venue_id="v", tenant_id="t", options={},
        r1_client=None, state_manager=state, event_publisher=Events(), activity_tracker=None,
        unit_number=None, batch_size=None,
    )
    phase = CreateApGroupsPhase(context)
    await phase.track_resource("passphrases", {
        "id": "abc", "username": "resident-1", "passphrase": "hunter2", "secret": "s3cret",
    })
    keys += [f"{sm.PREFIX}:jobs:{job}:activity", f"{sm.PREFIX}:jobs:{job}:activity:total"]
    raw = json.dumps((await state.get_activity(job))["rows"])
    check("name comes from the username", '"resident-1"' in raw, raw)
    check("no secret reaches the log", "hunter2" not in raw and "s3cret" not in raw, raw)

    class Broken:
        async def append_activity(self, *a):
            raise RuntimeError("redis is down")

    context.state_manager = Broken()
    try:
        await phase.record_change("created", "ap_groups", "x")
        check("a log failure doesn't fail the work", True)
    except Exception as e:
        check("a log failure doesn't fail the work", False, str(e))

    await redis.delete(*keys)
    print()
    print("FAILED: " + ", ".join(failures) if failures else "All checks passed")
    sys.exit(1 if failures else 0)


asyncio.run(main())
