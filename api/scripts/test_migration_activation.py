#!/usr/bin/env python3
"""
SZ -> R1 migration: SSID activation onto R1 AP Groups.

A run reported COMPLETED while R1 showed every activation failed:
  * activations targeted AP Groups that did not exist in R1 -- and when no
    R1 group matched by name, the SZ AP Group UUID was sent instead;
  * activation raced the DPSK link (WIFI-20300 on a DPSK network with no
    service yet);
  * every activation error was a warning, so the unit "succeeded".

WHAT THIS GUARDS

  1. SZ "default" maps to the venue's default R1 group (unnamed, isDefault).
  2. A named group with APs and no R1 match is planned for creation; a group
     with no APs is neither created nor activated; no SZ id is ever planned.
  3. Radios: 5/5L/5U -> 5-GHz; 6 GHz only for WPA3/OWE, reported otherwise.
  4. Activation sends step 1 ONCE with every target group, then steps 2-3
     per group; VLAN is inherited (none sent); the DPSK pool rides step 1.
  5. A group missing on read-back fails the unit; so does All AP Groups.
  6. Already-bound groups are skipped; a network already on the venue
     skips step 1 entirely.
  7. Created groups are resolved by name from create_ap_groups.
  8. Graph: activate_ssids after configure_dpsk; networks after AP Groups.
  9. The shared helper's default behaviour (Cloudpath import) is unchanged.

Usage:
    docker compose exec backend python scripts/test_migration_activation.py

Exits non-zero on failure.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import (
    SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZWLANGroup, SZWLANGroupMember,
    SZAPGroupEnriched,
)
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.ap_group_plan import plan_ap_groups, r1_radio_types
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.validate as validate_mod
import workflow.phases.sz_migration.activate_ssids as activate_mod
from workflow.workflows.sz_to_r1_migration import SZtoR1MigrationWorkflow
from r1api.services.venues import VenueService

SZ_DEFAULT_ID = "85e0850c-sz-default"
SZ_AG3_ID = "43f2d18b-sz-ag3"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def snapshot():
    wlans = [SZWLANFull(id="w1", name="Guest", ssid="Guest", auth_type="WPA2-PSK",
                        encryption={"method": "WPA2"}),
             SZWLANFull(id="w2", name="Secure6", ssid="Secure6", auth_type="WPA3-SAE",
                        encryption={"method": "WPA3"})]
    members = [SZWLANGroupMember(id=w.id, name=w.name, ssid=w.ssid) for w in wlans]
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone"),
        wlans=wlans,
        wlan_groups=[SZWLANGroup(id="wg-default", name="default", members=members)],
        ap_groups=[
            SZAPGroupEnriched(id=SZ_DEFAULT_ID, name="default", ap_count=15),
            SZAPGroupEnriched(id="ag2", name="AG2", ap_count=0),
            SZAPGroupEnriched(id=SZ_AG3_ID, name="AG3", ap_count=3),
        ],
    )


def inventory():
    return R1VenueInventory(venue_id="v", venue_name="V", tenant_id="t",
                            ap_groups=[{"id": "r1-default", "name": "", "isDefault": True}])


async def run_validate():
    snap = snapshot()

    class R:
        async def get(self, k):
            return snap.model_dump_json()

    async def fake_redis():
        return R()

    async def fake_inventory(*a, **k):
        return inventory()

    validate_mod.get_redis_client = fake_redis
    validate_mod.capture_venue_inventory = fake_inventory
    r1 = SimpleNamespace(radius_profiles=SimpleNamespace(get_radius_profiles=lambda t=None: []))
    phase = validate_mod.ValidateMigrationPhase(PhaseContext(
        job_id="j", r1_client=r1, venue_id="v", tenant_id="t", options={}))
    return await phase.execute(phase.Inputs(sz_snapshot_job_id="snap"))


# ==================== Fakes for the activation phase ====================

class FakeVenues:
    def __init__(self, world):
        self.world, self.calls = world, []

    async def activate_ssid_for_ap_group_direct(self, **kw):
        self.calls.append(kw)
        if kw["ap_group_id"] in self.world.get("fail_groups", ()):
            raise RuntimeError("Object not found")
        self.world.setdefault("bound", set()).add(kw["ap_group_id"])


class FakeNetworks:
    def __init__(self, world):
        self.world = world

    async def query_wifi_network_by_id(self, network_id, tenant_id=None):
        if self.world.get("venue_wide"):
            return {"venueApGroups": [{"venueId": "v", "isAllApGroups": True}]}
        bound = self.world.get("bound")
        if not bound:
            return {"venueApGroups": []}
        return {"venueApGroups": [{"venueId": "v", "isAllApGroups": False, "apGroupIds": sorted(bound)}]}


async def run_activate(activations, world, dpsk_pool_id=None, created=None):
    unit = SimpleNamespace(plan=SimpleNamespace(extra={"sz_wlan_name": "Guest", "activations": activations}))
    job = SimpleNamespace(global_phase_results={"create_ap_groups": {"ap_group_ids": created or {}}})

    class State:
        async def get_unit(self, *a):
            return unit

        async def get_job(self, *a):
            return job

    r1 = SimpleNamespace(venues=FakeVenues(world), networks=FakeNetworks(world))
    phase = activate_mod.ActivateSSIDsPhase(PhaseContext(
        job_id="j", r1_client=r1, venue_id="v", tenant_id="t", state_manager=State(), options={}))
    out = await phase.execute(phase.Inputs(unit_id="w1", unit_number="Guest", network_id="net-1",
                                           dpsk_pool_id=dpsk_pool_id))
    return out, r1.venues.calls


def act(gid, name, radios=("2.4-GHz", "5-GHz"), create=False):
    return {"r1_ap_group_id": gid, "r1_ap_group_name": name, "r1_ap_group_create": create,
            "r1_radio_types": list(radios)}


async def raises(coro):
    try:
        await coro
        return None
    except RuntimeError as e:
        return str(e)


async def main():
    failures = 0
    print("Migration SSID activation")

    # 1 + 2
    plan = plan_ap_groups(snapshot().ap_groups, inventory())
    failures += check("SZ 'default' -> R1 default group", plan["default"]["r1_id"] == "r1-default")
    failures += check("named group with APs and no R1 match -> create",
                      plan["AG3"]["create"] and plan["AG3"]["r1_id"] is None)
    failures += check("group with no APs -> not created", not plan["AG2"]["create"] and plan["AG2"]["empty"])

    out = await run_validate()
    acts = [a for u in out.unit_mappings.values() for a in u.plan.extra["activations"]]
    failures += check("no SZ AP Group id is ever planned as an R1 id",
                      all(a.get("r1_ap_group_id") not in (SZ_DEFAULT_ID, SZ_AG3_ID) for a in acts))
    failures += check("empty SZ group gets no activation", not any(a["ap_group_name"] == "AG2" for a in acts))
    failures += check("validation publishes groups to create", out.ap_groups_to_create == ["AG3"],
                      str(out.ap_groups_to_create))
    guest = [a for a in out.unit_mappings["w1"].plan.extra["activations"] if a["ap_group_name"] == "default"][0]
    failures += check("WPA2 WLAN on all SZ bands -> 2.4 + 5 GHz only",
                      guest["r1_radio_types"] == ["2.4-GHz", "5-GHz"], str(guest["r1_radio_types"]))
    secure = out.unit_mappings["w2"].plan.extra["activations"][0]
    failures += check("WPA3 WLAN keeps 6 GHz", "6-GHz" in secure["r1_radio_types"])
    warns = [c.description for c in out.validation_result.conflicts if c.severity == "warning"]
    failures += check("dropping 6 GHz is reported", any("6 GHz" in w and "Guest" in w for w in warns))

    # 3
    rt, notes = r1_radio_types(["2.4", "5L", "5U"], {"method": "WPA2"})
    failures += check("5L/5U -> one 5-GHz", rt == ["2.4-GHz", "5-GHz"] and not notes)

    # 4
    world = {}
    out, calls = await run_activate([act("g1", "default"), act("g2", "AG3", ("5-GHz",))], world,
                                    dpsk_pool_id="pool-1")
    failures += check("step 1 once, carrying every group",
                      len(calls) == 2 and [g["apGroupId"] for g in calls[0]["step1_ap_groups"]] == ["g1", "g2"]
                      and calls[1]["skip_step1"] and not calls[0]["skip_step1"])
    failures += check("radios per group", calls[1]["radio_types"] == ["5-GHz"])
    failures += check("VLAN inherited, DPSK pool sent",
                      all(c["vlan_id"] is None and c["dpsk_service_id"] == "pool-1" for c in calls))
    failures += check("success when every group reads back", out.activated and out.activations_succeeded == 2)

    # 5
    msg = await raises(run_activate([act("g1", "default"), act("g2", "AG3")], {"fail_groups": {"g2"}}))
    failures += check("a group missing on read-back fails the unit", msg is not None and "AG3" in msg, str(msg))
    msg = await raises(run_activate([act("g1", "default")], {"venue_wide": True}))
    failures += check("All AP Groups fails the unit", msg is not None and "All AP Groups" in msg)

    # 6
    out, calls = await run_activate([act("g1", "default")], {"bound": {"g1"}})
    failures += check("already-bound group -> no calls", not calls and out.already_active == 1)
    out, calls = await run_activate([act("g1", "default"), act("g2", "AG3")], {"bound": {"g1"}})
    failures += check("network already on the venue -> only steps 2-3 for the new group",
                      len(calls) == 1 and calls[0]["ap_group_id"] == "g2" and calls[0]["skip_step1"])

    # 7
    out, calls = await run_activate([act(None, "AG3", create=True)], {}, created={"AG3": "g-new"})
    failures += check("created group resolved by name", calls and calls[0]["ap_group_id"] == "g-new")
    msg = await raises(run_activate([act(None, "AG3", create=True)], {}, created={}))
    failures += check("a group that was not created fails clearly", msg is not None and "not created" in msg)

    # 8
    deps = {p.id: p.depends_on for p in SZtoR1MigrationWorkflow.phases}
    failures += check("activate_ssids runs after configure_dpsk", deps["activate_ssids"] == ["configure_dpsk"])
    failures += check("networks after AP Groups", deps["create_networks"] == ["create_ap_groups"])

    # 9 shared helper unchanged by default (Cloudpath import path)
    class Resp:
        status_code, content = 200, b""

        def json(self):
            return {}

    puts = []
    client = SimpleNamespace(ec_type="EC", put=lambda path, payload=None: (puts.append((path, payload)), Resp())[1])
    await VenueService(client).activate_ssid_for_ap_group_direct(
        tenant_id="t", venue_id="v", wifi_network_id="n", ap_group_id="g")
    failures += check("default call: 3 PUTs, step 1 with the single group",
                      len(puts) == 3 and puts[0][0] == "/venues/v/wifiNetworks/n"
                      and [g["apGroupId"] for g in puts[0][1]["apGroups"]] == ["g"])

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
