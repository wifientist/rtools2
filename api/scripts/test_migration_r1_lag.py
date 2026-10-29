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
  3. Create: task timed out but landed -> found, not re-created.
  4. Create: 503 and not landed -> retried, created on the 2nd try.
  5. Create: a real refusal fails at once, no retry.
  6. Create: the retry is refused "already exists" because the first create
     landed meanwhile -> that network is used.
  7. Activate (groups): not bound after the first read-back, but the task
     lands later -> found by the longer read-back, no re-activation.
  8. Activate (groups): "Object not found" the first time -> retried once, active.
  9. Activate (groups): a non-transient error -> no retry, fails.
 10. Activate (All AP Groups): 503 the first time -> retried, active.

Usage:
    docker compose exec backend python scripts/test_migration_r1_lag.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.ap_group_ops import is_transient
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
        elif step == "notfound":
            raise RuntimeError("Task a failed: Object not found")
        else:
            raise RuntimeError("Task a failed: Invalid radio type for this AP Group")

    async def activate_ssid_on_venue(self, **kw):
        self.calls += 1
        step = self.script.pop(0)
        if step == "503":
            raise RuntimeError("503 Server Error: Service Unavailable for url: https://api.ruckus.cloud/x")
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
    nets_script = ["timeout", "exists"]
    nid, n, err = await run_create(nets_script, visible_after=10 ** 6)
    f += check("(a timeout that never lands, then a real refusal: fails)", nid is None and n == 2, str((nid, n, err)))

    # 6: first create times out, lands invisibly-late; the retry is refused "already exists"
    nets = FakeNets(["timeout", "exists"])
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

    print(f"\n{'All checks passed' if not f else f'{f} check(s) failed'}")
    return f


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
