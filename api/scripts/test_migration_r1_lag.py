#!/usr/bin/env python3
"""
SZ -> R1 migration: riding out R1's lag on big runs.

Every run of 300-600 WLANs failed 1-10 of them, and a re-run always picked
them up. From the jobs' own errors (2026-10-26..29): "Network creation
returned no ID" (task done, name lookup ran before R1's index had it), task
"did not complete after 40 attempts (97 seconds)" (it landed later), 503 /
RCG-10000 "System might be busy ... no healthy upstream", activation "Object
not found", and an All AP Groups read-back still 'not_found' after ~45s.

WHAT THIS GUARDS
  1. is_transient: each of those real messages is transient; a real refusal
     (duplicate name, WIFI-20300) is not; a unit named "5030" is not a 503.
  2. Create: no id from the create -> found by name once R1 has it.
  3. Create: task still running after the wait but landed -> found, not re-created;
     still running and not landed -> skipped, never re-sent.
  4. Create: 503 and not landed -> retried, created on the 2nd try.
  5. Create: a real refusal fails at once, no retry.
  6. Create: the retry is refused "already exists" because the first create
     landed meanwhile -> that network is used.
  7. Activate (groups): not bound after the first read-back, but the task
     lands later -> found by the longer read-back, no re-activation.
  8. Activate (groups): "Object not found" the first time -> retried once, active.
  9. Activate (groups): a non-transient error -> no retry, fails.
 10. Activate (All AP Groups): 503 the first time -> retried, active.
 10b. A task still running after the ~5 min wait (groups or All AP Groups),
     or one R1 said SUCCESS for -> never re-sent.
 11. The activity wait itself: "invalid id" waited out, minutes IN_PROGRESS
     waited out, ~5 min cap, ~2 min for a never-registered activity, FAIL at once.

Usage:
    docker compose exec backend python scripts/test_migration_r1_lag.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import r1api.client as client_mod
from services.ap_group_ops import is_transient, still_running
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.activate_ssids as activate_mod
import workflow.phases.sz_migration.create_networks as create_mod

_real_sleep = asyncio.sleep


async def _no_sleep(*_a, **_k):
    await _real_sleep(0)

asyncio.sleep = _no_sleep          # the phases' waits are R1's, not ours


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


class State:
    def __init__(self, extra):
        self.unit = SimpleNamespace(plan=SimpleNamespace(extra=extra))
        self.job = SimpleNamespace(global_phase_results={}, input_data={})

    async def get_unit(self, *a):
        return self.unit

    async def get_job(self, *a):
        return self.job


# ── Create ────────────────────────────────────────────────────────────

class FakeNets:
    """script: per create call, 'ok' | 'noid' | 'timeout_landed' | 'timeout' | '503' | 'refuse' | 'exists'.
    visible_after: name lookups that miss before a landed network shows up."""

    def __init__(self, script, visible_after=0):
        self.script, self.visible_after = list(script), visible_after
        self.creates, self.lookups, self.landed = 0, 0, False

    async def find_wifi_network_by_name(self, tenant_id, venue_id, name):
        self.lookups += 1
        if self.landed and self.lookups > self.visible_after:
            return {"id": "net-1", "name": name}
        return None

    async def create_wifi_network(self, **kw):
        self.creates += 1
        step = self.script.pop(0)
        if step == "ok":
            self.landed = True
            return {"id": "net-1"}
        if step == "noid":
            self.landed = True
            return {"requestId": "r"}
        if step == "timeout_landed":
            self.landed = True
            raise TimeoutError("Task t did not complete after 40 attempts (97 seconds)")
        if step == "timeout":
            raise TimeoutError("Task t did not complete after 40 attempts (97 seconds)")
        if step == "503":
            raise RuntimeError("R1 503 - System might be busy, temporarily unavailable. Details: no healthy upstream")
        if step == "exists":
            raise RuntimeError("400 - Network name 'Guest' already exists")
        raise RuntimeError("400 - WIFI-20300: invalid security settings")


async def run_create(script, visible_after=0):
    nets = FakeNets(script, visible_after)
    lookups_before = {"n": 0}
    orig = nets.find_wifi_network_by_name

    # The phase's own "already exists?" check comes first: it must miss.
    async def find(*a):
        if lookups_before["n"] == 0:
            lookups_before["n"] = 1
            return None
        return await orig(*a)
    nets.find_wifi_network_by_name = find
    r1 = SimpleNamespace(networks=nets)
    state = State({"sz_wlan_name": "Guest", "ssid": "Guest", "r1_network_type": "psk", "vlan_id": 1,
                   "sz_auth_type": "WPA2-PSK", "encryption": {"passphrase": "x" * 12}})
    phase = create_mod.CreateNetworksPhase(PhaseContext(
        job_id="j", r1_client=r1, venue_id="v", tenant_id="t", state_manager=state, options={}))

    async def track(*a, **k):
        return None
    phase.track_resource = track
    try:
        out = await phase.execute(phase.Inputs(unit_id="w1", unit_number="Guest"))
        return out.network_id, nets.creates, None
    except RuntimeError as e:
        return None, nets.creates, str(e)


# ── Activate ──────────────────────────────────────────────────────────

class FakeVenues:
    """script: per activation call (per group), 'ok' | 'late' (lands after `late_reads` reads) |
    'notfound' | 'refuse' | '503'."""

    def __init__(self, world, script):
        self.world, self.script, self.calls = world, list(script), 0

    async def activate_ssid_for_ap_group_direct(self, **kw):
        self.calls += 1
        step = self.script.pop(0)
        if step == "ok":
            self.world["bound"].add(kw["ap_group_id"])
        elif step == "late":
            self.world["late"] = (kw["ap_group_id"], self.world["reads"] + self.world["late_reads"])
            raise TimeoutError("Task a did not complete after 40 attempts (97 seconds)")
        elif step == "pending":
            raise TimeoutError("Task a did not complete after 300s (R1 still reports it IN_PROGRESS)")
        elif step == "hidden":
            pass                         # R1 said SUCCESS; the read-back never shows it
        elif step == "notfound":
            raise RuntimeError("Task a failed: Object not found")
        else:
            raise RuntimeError("Task a failed: Invalid radio type for this AP Group")

    async def activate_ssid_on_venue(self, **kw):
        self.calls += 1
        step = self.script.pop(0)
        if step == "503":
            raise RuntimeError("503 Server Error: Service Unavailable for url: https://api.ruckus.cloud/x")
        if step == "pending":
            raise TimeoutError("Task v did not complete after 300s (R1 still reports it INPROGRESS)")
        self.world["venue_wide"] = True
        return {}


class FakeQuery:
    def __init__(self, world):
        self.world = world

    async def query_wifi_network_by_id(self, network_id, tenant_id=None):
        w = self.world
        w["reads"] += 1
        if w.get("late") and w["reads"] >= w["late"][1]:
            w["bound"].add(w["late"][0])
        if w.get("venue_wide"):
            return {"venueApGroups": [{"venueId": "v", "isAllApGroups": True}]}
        if not w["bound"]:
            return {"venueApGroups": []}
        return {"venueApGroups": [{"venueId": "v", "isAllApGroups": False, "apGroupIds": sorted(w["bound"])}]}


async def run_activate(script, mode="ap_groups", late_reads=0):
    world = {"bound": set(), "reads": 0, "late_reads": late_reads}
    extra = {"sz_wlan_name": "Guest", "activation_mode": mode, "all_ap_radio_types": ["2.4-GHz", "5-GHz"],
             "activations": [{"r1_ap_group_id": "g1", "r1_ap_group_name": "101", "r1_radio_types": ["5-GHz"]}]}
    venues = FakeVenues(world, script)
    r1 = SimpleNamespace(venues=venues, networks=FakeQuery(world))
    phase = activate_mod.ActivateSSIDsPhase(PhaseContext(
        job_id="j", r1_client=r1, venue_id="v", tenant_id="t", state_manager=State(extra), options={}))
    try:
        await phase.execute(phase.Inputs(unit_id="w1", unit_number="Guest", network_id="net-1"))
        return venues.calls, None
    except RuntimeError as e:
        return venues.calls, str(e)


async def wait_on(script, **kw):
    """Run await_task_completion against /activities answers in script (one per poll
    cycle, 3s apart; the last repeats), on a fake clock. -> (result | exception, polls, notices)."""
    from r1api.client import R1Client
    loop = asyncio.get_running_loop()
    clock = {"t": 0.0}
    real_time = loop.time
    polls, notices = [], []

    async def next_read(request_id, tenant_id=None):
        clock["t"] += client_mod.ACTIVITY_POLL_SECONDS
        await _real_sleep(0)
        step = script[min(len(polls), len(script) - 1)]
        polls.append(clock["t"])
        return None if step == "invalid" else {"status": step, "error": "Bad radio"}

    async def listener(rid, status, elapsed):
        notices.append((status, round(elapsed)))

    c = R1Client.__new__(R1Client)
    c._next_activity_read = next_read
    loop.time = lambda: clock["t"]
    token = client_mod.activity_wait_listener.set(listener)
    try:
        try:
            out = await c.await_task_completion("r1", **kw)
        except Exception as e:
            out = e
    finally:
        client_mod.activity_wait_listener.reset(token)
        loop.time = real_time
    return out, polls, notices


def fake_r1(statuses, *, bulk_fails=False, bulk_delay=0.0, hide=()):
    """An R1Client whose POST /activities/query and GET /activities/{id} answer from
    statuses {requestId: [status per bulk query, last repeats]} -- blocking, like requests."""
    import time as _time
    from r1api.client import R1Client
    c = R1Client.__new__(R1Client)
    calls = {"bulk": 0, "get": []}

    def resp(ok, body):
        return SimpleNamespace(ok=ok, status_code=200 if ok else 400, text="", json=lambda: body)

    def status_of(rid):
        seq = statuses[rid]
        return seq[min(calls["bulk"] - 1, len(seq) - 1)]

    def post(path, payload=None, override_tenant_id=None):
        calls["bulk"] += 1
        _time.sleep(bulk_delay)
        if bulk_fails:
            return resp(False, {})
        rows = [{"requestId": rid, "status": status_of(rid)} for rid in statuses if rid not in hide]
        return resp(True, {"data": rows, "totalCount": len(rows)})

    def get(path, override_tenant_id=None):
        rid = path.rsplit("/", 1)[-1]
        calls["get"].append(rid)
        st = status_of(rid) if calls["bulk"] else "INPROGRESS"
        return resp(True, {"requestId": rid, "status": st, "error": f"{rid} refused"})

    c.post, c.get = post, get
    return c, calls


async def test_shared_poller():
    f = 0
    saved = client_mod.ACTIVITY_POLL_SECONDS, client_mod.ACTIVITY_BULK_MISS_SECONDS
    client_mod.ACTIVITY_POLL_SECONDS = 0.05
    try:
        # 200 waits at once -> a handful of bulk queries, not 200 x N GETs
        ids = [f"r{i}" for i in range(200)]
        c, calls = fake_r1({rid: ["INPROGRESS", "INPROGRESS", "SUCCESS"] for rid in ids})
        out = await asyncio.gather(*(c.await_task_completion(rid) for rid in ids))
        f += check("200 concurrent waits share one bulk query per cycle",
                   all(o["status"] == "SUCCESS" for o in out) and calls["bulk"] <= 4,
                   f"bulk={calls['bulk']} gets={len(calls['get'])}")
        f += check("a task that succeeds is answered from the bulk query, with no read of its own",
                   len(calls["get"]) == 0, str(len(calls["get"])))

        # FAIL carries R1's error from the full record
        c, _ = fake_r1({"ok": ["SUCCESS"], "bad": ["INPROGRESS", "FAIL"]})
        res = await asyncio.gather(c.await_task_completion("ok"), c.await_task_completion("bad"),
                                   return_exceptions=True)
        f += check("FAIL -> that wait raises with R1's error, the other succeeds",
                   res[0]["status"] == "SUCCESS" and "bad refused" in str(res[1])
                   and not isinstance(res[1], TimeoutError), repr(res))

        # A slow (blocking) query never blocks the event loop
        c, _ = fake_r1({"slow": ["SUCCESS"]}, bulk_delay=0.4)
        ticks = []

        async def ticker():
            for _ in range(8):
                ticks.append(asyncio.get_running_loop().time())
                await _real_sleep(0.05)
        t = asyncio.create_task(ticker())
        await c.await_task_completion("slow")
        await t
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        f += check("bulk query runs off the event loop (other tasks keep ticking)",
                   max(gaps) < 0.25, f"max gap {max(gaps):.2f}s")

        # Query refused -> falls back to one-by-one reads
        c, calls = fake_r1({"a": ["SUCCESS"], "b": ["SUCCESS"]}, bulk_fails=True)
        out = await asyncio.gather(c.await_task_completion("a"), c.await_task_completion("b"))
        f += check("query refused -> read one by one, still finishes",
                   all(o["status"] == "SUCCESS" for o in out) and set(calls["get"]) >= {"a", "b"},
                   str(calls))

        # Query never shows one -> read on its own after a while
        client_mod.ACTIVITY_BULK_MISS_SECONDS = 0.1
        c, calls = fake_r1({"seen": ["SUCCESS"], "lost": ["SUCCESS"]}, hide={"lost"})
        out = await asyncio.gather(c.await_task_completion("seen"), c.await_task_completion("lost"))
        f += check("one the query misses is read on its own",
                   out[1]["status"] == "SUCCESS" and "lost" in calls["get"], str(calls))
        await _real_sleep(0.2)
        watches = list(c.__dict__["_activity_watches"].values())
        f += check("poller stops once nothing is waited on",
                   all(not w.waiting and w.task.done() for w in watches), str(len(watches)))
    finally:
        client_mod.ACTIVITY_POLL_SECONDS, client_mod.ACTIVITY_BULK_MISS_SECONDS = saved
    return f


async def test_activity_wait():
    f = 0
    # "invalid id" for the first seconds, IN_PROGRESS for ~4 min, then SUCCESS
    script = ["invalid"] * 4 + ["IN_PROGRESS"] * 45 + ["SUCCESS"]
    out, polls, notices = await wait_on(script)
    f += check("invalid id at first, then minutes IN_PROGRESS -> waited out, SUCCESS returned",
               isinstance(out, dict) and out.get("status") == "SUCCESS" and polls[-1] > 97,
               f"{out!r} after {polls[-1] if polls else 0:.0f}s")
    f += check("long wait reported to the listener about once a minute",
               len(notices) >= 2 and notices[0][0] == "IN_PROGRESS", str(notices))
    out, polls, _ = await wait_on(["IN_PROGRESS"])
    f += check("never finishes -> ActivityPending (a TimeoutError) after ~5 min, not before",
               isinstance(out, client_mod.ActivityPending) and isinstance(out, TimeoutError)
               and 300 <= polls[-1] < 320 and is_transient(out) and still_running(out),
               f"{out!r} after {polls[-1]:.0f}s")
    out, polls, _ = await wait_on(["invalid"])
    f += check("never registered -> stops after ~2 min",
               isinstance(out, client_mod.ActivityPending) and 120 <= polls[-1] < 135 and "never showed" in str(out),
               f"{out!r} after {polls[-1]:.0f}s")
    out, _, _ = await wait_on(["invalid"], assume_success_on_timeout=True)
    f += check("never registered + assume_success_on_timeout -> ASSUMED_SUCCESS",
               isinstance(out, dict) and out.get("status") == "ASSUMED_SUCCESS", repr(out))
    out, polls, _ = await wait_on(["IN_PROGRESS", "FAIL"])
    f += check("FAIL -> raises with R1's error at once, not a pending",
               not isinstance(out, TimeoutError) and "Bad radio" in str(out) and len(polls) == 2, repr(out))
    out, polls, _ = await wait_on(["IN_PROGRESS"], max_attempts=3)
    f += check("max_attempts given -> that many polls", isinstance(out, TimeoutError) and len(polls) == 3,
               str(len(polls)))
    return f


async def main():
    f = 0
    print("\n1. is_transient (messages from the real jobs)")
    for msg, want in (
        ("Task 9fa60b86 did not complete after 40 attempts (97 seconds)", True),
        ("'1404': 503 Server Error: Service Unavailable for url: https://api.ruckus.cloud/venues/e2", True),
        ('Failed to create DPSK network: 400 - {"errors":[{"code":"RCG-10000","message":"System might be busy"}]}', True),
        ("System might be busy, temporarily unavailable. Details: no healthy upstream", True),
        ("'4311': Task 5336 failed: Object not found", True),
        ("Network name '5030@Site' already exists", False),
        ("WIFI-20300: the DPSK network has no DPSK service", False),
    ):
        f += check(f"{'transient' if want else 'real refusal'}: {msg[:60]}", is_transient(msg) == want)

    print("\n2-6. Create")
    nid, n, err = await run_create(["noid"], visible_after=3)
    f += check("no id from the create -> found by name once R1 has it", nid == "net-1" and n == 1 and not err,
               str((nid, n, err)))
    nid, n, err = await run_create(["timeout_landed"], visible_after=2)
    f += check("timed out but landed -> found, not re-created", nid == "net-1" and n == 1, str((nid, n, err)))
    nid, n, err = await run_create(["503", "ok"])
    f += check("503, not landed -> retried, created on the 2nd try", nid == "net-1" and n == 2, str((nid, n, err)))
    nid, n, err = await run_create(["503", "503", "503"])
    f += check("transient three times -> fails, says how often it tried",
               nid is None and n == 3 and "tried 3 times" in (err or ""), str((nid, n, err)))
    nid, n, err = await run_create(["refuse"])
    f += check("a real refusal fails at once", nid is None and n == 1 and "WIFI-20300" in (err or ""), str(err))
    nid, n, err = await run_create(["timeout"], visible_after=10 ** 6)
    f += check("still running after the wait, never seen -> skipped, NOT re-sent",
               nid is None and n == 1 and "not re-sent" in (err or ""), str((nid, n, err)))

    # 6: first create refused 503 but lands invisibly-late; the retry is refused "already exists"
    nets = FakeNets(["503", "exists"])
    orig_create = nets.create_wifi_network

    async def create_then_land(**kw):
        try:
            return await orig_create(**kw)
        finally:
            if nets.creates == 1:
                pass
            else:
                nets.landed = True        # it had landed by the time the retry was refused
    nets.create_wifi_network = create_then_land
    seen = {"n": 0}
    orig_find = nets.find_wifi_network_by_name

    async def find(*a):
        seen["n"] += 1
        return None if seen["n"] == 1 else await orig_find(*a)
    nets.find_wifi_network_by_name = find
    phase = create_mod.CreateNetworksPhase(PhaseContext(
        job_id="j", r1_client=SimpleNamespace(networks=nets), venue_id="v", tenant_id="t",
        state_manager=State({"sz_wlan_name": "Guest", "ssid": "Guest", "r1_network_type": "psk",
                             "encryption": {"passphrase": "x" * 12}}), options={}))

    async def track(*a, **k):
        return None
    phase.track_resource = track
    out = await phase.execute(phase.Inputs(unit_id="w1", unit_number="Guest"))
    f += check("retry refused 'already exists' because the 1st landed -> that network is used",
               out.network_id == "net-1" and nets.creates == 2, str((out, nets.creates)))

    print("\n7-10. Activate")
    calls, err = await run_activate(["late"], late_reads=9)
    f += check("bound late (after the first ~45s of read-back) -> found by the longer look, not re-activated",
               err is None and calls == 1, str((calls, err)))
    calls, err = await run_activate(["notfound", "ok"])
    f += check("'Object not found' -> retried once, active", err is None and calls == 2, str((calls, err)))
    calls, err = await run_activate(["refuse"])
    f += check("a real refusal -> no retry, fails with R1's reason",
               calls == 1 and "Invalid radio type" in (err or ""), str((calls, err)))
    calls, err = await run_activate(["503", "ok"], mode="all_ap_groups")
    f += check("All AP Groups: 503 -> retried, active", err is None and calls == 2, str((calls, err)))
    calls, err = await run_activate(["pending"])
    f += check("still running after the wait, never lands -> skipped, NOT re-sent",
               calls == 1 and "not re-sent" in (err or ""), str((calls, err)))
    calls, err = await run_activate(["hidden"])
    f += check("R1 said SUCCESS but the read-back never shows it -> fails, NOT re-sent",
               calls == 1 and err is not None, str((calls, err)))
    calls, err = await run_activate(["pending"], mode="all_ap_groups")
    f += check("All AP Groups: still running after the wait -> skipped, NOT re-sent",
               calls == 1 and "not re-sent" in (err or ""), str((calls, err)))

    print("\n11. The activity wait (r1api.client.await_task_completion)")
    f += await test_activity_wait()

    print("\n12. The shared activity poller (one bulk query for every wait)")
    f += await test_shared_poller()

    print(f"\n{'All checks passed' if not f else f'{f} check(s) failed'}")
    return f


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
