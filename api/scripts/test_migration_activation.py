#!/usr/bin/env python3
"""
SZ -> R1 migration: R1 placement and SSID activation.

A run reported COMPLETED while R1 showed every activation failed:
  * activations targeted AP Groups that did not exist in R1 -- and when no
    R1 group matched by name, the SZ AP Group UUID was sent instead;
  * activation raced the DPSK link (WIFI-20300 on a DPSK network with no
    service yet);
  * every activation error was a warning, so the unit "succeeded".

Placement is derived from what each AP broadcasts in SZ (AP override > AP
Group override > zone > "default" WLAN Group), not from SZ's AP Groups.

WHAT THIS GUARDS

  1. WLANs on >= 90% of broadcasting APs are suggested for All AP Groups;
     the operator's choice overrides the suggestion.
  2. APs left with the same WLANs form one R1 AP Group, named after their
     shared AP-level WLAN Group; matched to R1 by name, created only when
     ticked; a WLAN aimed at an un-created group is not activated there
     (reported). No SZ id is ever planned as an R1 id. An R1 group already
     carrying exactly the group's SSIDs is reused whatever its name; an R1
     snapshot missing groups that networks are activated on blocks.
  3. WLANs no AP broadcasts are not migrated unless ticked (then created,
     not activated). Over 15 SSIDs on an AP Group blocks the plan.
  3b. Radios: 5/5L/5U -> 5-GHz; 6 GHz only for WPA3/OWE, reported otherwise.
  4. Activation sends step 1 ONCE with every target group, then steps 2-3
     per group; VLAN is inherited (none sent); the DPSK pool rides step 1.
  5. A group missing on read-back fails the unit; so does All AP Groups.
  6. Already-bound groups are skipped; a network already on the venue
     skips step 1 entirely.
  7. Created groups are resolved by name from create_ap_groups.
  8. Graph: activate_ssids after configure_dpsk; networks after AP Groups.
  9. The shared helper's default behaviour (Cloudpath import) is unchanged.
 10. All AP Groups WLANs: venue-wide activation, read back as isAllApGroups;
     a network already on specific groups is not widened.

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
from services.sz_migration.ap_group_plan import r1_radio_types
from services.sz_migration.r1_layout import build_layout, group_key
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.validate as validate_mod
import workflow.phases.sz_migration.activate_ssids as activate_mod
from workflow.workflows.sz_to_r1_migration import SZtoR1MigrationWorkflow
from r1api.services.venues import VenueService

SZ_DEFAULT_ID = "85e0850c-sz-default"
SZ_AG3_ID = "43f2d18b-sz-ag3"
UNIT_KEY = group_key(frozenset({"w3"}))


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def snapshot():
    """10 APs on 2.4/5/6 GHz broadcasting the zone default (Guest, Secure6);
    one of them has an AP-level override to 'unit-101' (+ Unit101). 'Unused'
    is in a WLAN Group nothing uses."""
    wlans = [SZWLANFull(id="w1", name="Guest", ssid="Guest", auth_type="WPA2-PSK",
                        encryption={"method": "WPA2"}),
             SZWLANFull(id="w2", name="Secure6", ssid="Secure6", auth_type="WPA3-SAE",
                        encryption={"method": "WPA3"}),
             SZWLANFull(id="w3", name="Unit101", ssid="Unit101", auth_type="WPA2-PSK",
                        encryption={"method": "WPA2"}),
             SZWLANFull(id="w4", name="Unused", ssid="Unused", auth_type="WPA2-PSK")]
    m = {w.id: SZWLANGroupMember(id=w.id, name=w.name, ssid=w.ssid) for w in wlans}
    aps = [{"apMac": f"ap{i}", "deviceName": f"AP-{i}", "serial": f"S{i}", "apGroupId": SZ_DEFAULT_ID}
           for i in range(10)]
    radios = lambda gid: {b: {"wlan_group_id": gid, "service_enabled": True}
                          for b in ("radio24g", "radio5g", "radio6g")}
    detail = {f"ap{i}": {"dual5g": False, "radios": radios("wg-101" if i == 0 else None)} for i in range(10)}
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone"),
        wlans=wlans,
        wlan_groups=[SZWLANGroup(id="wg-default", name="default", members=[m["w1"], m["w2"]]),
                     SZWLANGroup(id="wg-101", name="unit-101", members=[m["w1"], m["w2"], m["w3"]]),
                     SZWLANGroup(id="wg-x", name="unused", members=[m["w4"]])],
        ap_groups=[SZAPGroupEnriched(id=SZ_DEFAULT_ID, name="default", ap_count=10),
                   SZAPGroupEnriched(id=SZ_AG3_ID, name="AG3", ap_count=0)],
        aps=aps,
        ap_radio_config=detail,
    )


def inventory(groups=()):
    return R1VenueInventory(venue_id="v", venue_name="V", tenant_id="t",
                            ap_groups=[{"id": "r1-default", "name": "", "isDefault": True}, *groups])


async def run_validate(options=None, inv=None):
    snap = snapshot()

    class R:
        async def get(self, k):
            return snap.model_dump_json()

    async def fake_redis():
        return R()

    async def fake_inventory(*a, **k):
        return inv or inventory()

    validate_mod.get_redis_client = fake_redis
    validate_mod.capture_venue_inventory = fake_inventory
    r1 = SimpleNamespace(radius_profiles=SimpleNamespace(get_radius_profiles=lambda t=None: []))
    phase = validate_mod.ValidateMigrationPhase(PhaseContext(
        job_id="j", r1_client=r1, venue_id="v", tenant_id="t", options=options or {}))
    return await phase.execute(phase.Inputs(sz_snapshot_job_id="snap"))


# ==================== Fakes for the activation phase ====================

class FakeVenues:
    def __init__(self, world):
        self.world, self.calls = world, []

    async def activate_ssid_on_venue(self, **kw):
        self.calls.append({"venue_wide": True, **kw})
        self.world["venue_wide"] = True
        return {"requestId": "r1"}

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


async def run_activate(activations, world, dpsk_pool_id=None, created=None, mode="ap_groups"):
    unit = SimpleNamespace(plan=SimpleNamespace(extra={
        "sz_wlan_name": "Guest", "activations": activations, "activation_mode": mode,
        "all_ap_radio_types": ["2.4-GHz", "5-GHz"]}))
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

    # 1
    lay = build_layout(snapshot(), inventory())
    rows = {w["id"]: w for w in lay["wlans"]}
    failures += check("WLANs on every AP suggested for All AP Groups",
                      rows["w1"]["suggested_all_ap"] and rows["w2"]["suggested_all_ap"]
                      and rows["w1"]["mode"] == "all_ap_groups")
    failures += check("unit WLAN (1 of 10 APs) is not", rows["w3"]["mode"] == "ap_groups"
                      and not rows["w3"]["suggested_all_ap"] and rows["w3"]["sz_ap_count"] == 1)
    lay2 = build_layout(snapshot(), inventory(), {"all_ap_wlans": []})
    g2 = {tuple(g["wlan_ids"]): g for g in lay2["groups"]}
    failures += check("choice overrides suggestion: nothing All AP -> groups carry the common WLANs",
                      set(g2) == {("w1", "w2"), ("w1", "w2", "w3")}
                      and g2[("w1", "w2")]["ap_count"] == 9, str(list(g2)))

    # 2
    g = lay["groups"]
    failures += check("one group: the AP left with Unit101, named after its AP-level WLAN Group",
                      len(g) == 1 and g[0]["name"] == "unit-101" and g[0]["ap_count"] == 1
                      and g[0]["aps"][0]["serial"] == "S0" and g[0]["key"] == UNIT_KEY, str(g))
    failures += check("other 9 APs need only All AP Groups (default group)", lay["summary"]["aps_default_group"] == 9)
    failures += check("not in R1, not ticked -> pending, Unit101 not ready",
                      not g[0]["exists"] and not g[0]["create"] and rows["w3"]["targets_ready"] == 0
                      and "unready_note" in rows["w3"])

    out = await run_validate()
    failures += check("un-ticked group: nothing to create", out.ap_groups_to_create == [])
    warns = [c.description for c in out.validation_result.conflicts if c.severity == "warning"]
    failures += check("un-ticked group reported on the WLAN", any("Unit101" in w and "unit-101" in w for w in warns),
                      str(warns))
    failures += check("Unit101 has no activation targets", out.unit_mappings["w3"].plan.extra["activations"] == [])

    out = await run_validate({"layout_choices": {"create_groups": [UNIT_KEY]}})
    failures += check("ticked group -> created", out.ap_groups_to_create == ["unit-101"], str(out.ap_groups_to_create))
    t = out.unit_mappings["w3"].plan.extra["activations"]
    failures += check("Unit101 aimed at the group to create",
                      len(t) == 1 and t[0]["r1_ap_group_create"] and t[0]["r1_ap_group_id"] is None
                      and t[0]["r1_ap_group_name"] == "unit-101", str(t))

    # Staged: a WLAN no AP broadcasts, created with its paired AP Group -- built
    # here with the rest of the configuration, never activated.
    out = await run_validate({"layout_choices": {
        "stage_unbroadcast": ["w4"],
        "pairing": {"mode": "pattern", "wlan_pattern": "{unit}", "ap_pattern": "AP-5"}}})
    failures += check("staged group created by Create WLANs, beside ticked ones",
                      out.ap_groups_to_create == ["Unused"], str(out.ap_groups_to_create))
    failures += check("staged WLAN migrated without activation",
                      "w4" in out.unit_mappings and out.unit_mappings["w4"].plan.extra.get("activation_mode") == "none",
                      str(out.unit_mappings.get("w4") and out.unit_mappings["w4"].plan.extra))

    # A network an earlier run created but never activated here is only in the
    # tenant list: the plan reuses it (as create_networks would), and flags a
    # different type instead of claiming "reuse" silently.
    tenant_only = inventory()
    tenant_only.tenant_networks = [{"id": "n-guest", "name": "Guest", "ssid": "Guest", "nwSubType": "psk"},
                                   {"id": "n-sec6", "name": "Secure6", "ssid": "Secure6", "nwSubType": "dpsk"}]
    out = await run_validate(inv=tenant_only)
    acts = {a.name: a for a in out.validation_result.actions if a.resource_type == "wifi_network"}
    failures += check("network only in the tenant (not activated here) is reused, not created",
                      acts["Guest"].action == "reuse" and acts["Guest"].existing_id == "n-guest"
                      and out.unit_mappings["w1"].resolved.network_id == "n-guest", str(acts.get("Guest")))
    warns = [c.description for c in out.validation_result.conflicts if c.resource_type == "wifi_network"]
    failures += check("reused as a different type is flagged",
                      any("Secure6" in w and "DPSK" in w for w in warns), str(warns))

    # Name clashes: R1 treats names differing only in case as the same, and
    # another property's network of the same name is not ours to reuse.
    clash = inventory()
    clash.tenant_networks = [
        {"id": "n-other", "name": "guest", "ssid": "guest", "nwSubType": "psk", "venueIds": ["other-venue"]},
        {"id": "n-theirs", "name": "Secure6", "ssid": "Secure6", "nwSubType": "psk", "venueIds": ["other-venue"]},
        {"id": "n-ours", "name": "Unit101", "ssid": "Unit101", "nwSubType": "psk", "venueIds": []},
    ]
    out = await run_validate(inv=clash)
    ex = {u.plan.extra["sz_wlan_name"]: u for u in out.unit_mappings.values()}
    failures += check("case-only clash with another property's network: created as <name>_<venue>, SSID unchanged",
                      ex["Guest"].plan.extra["r1_network_name"] == "Guest_V" and ex["Guest"].plan.extra["ssid"] == "Guest"
                      and not ex["Guest"].resolved.network_id, str(ex["Guest"].plan.extra.get("r1_network_name")))
    failures += check("exact name held by another venue's network: not reused, renamed",
                      ex["Secure6"].plan.extra["r1_network_name"] == "Secure6_V" and not ex["Secure6"].resolved.network_id)
    failures += check("an earlier run's network (activated nowhere) is reused under its name",
                      ex["Unit101"].resolved.network_id == "n-ours" and ex["Unit101"].plan.extra["r1_network_name"] == "Unit101")
    warns = [c.description for c in out.validation_result.conflicts if c.resource_type == "wifi_network"]
    failures += check("the rename is explained in the plan",
                      any("Guest" in w and "created as 'Guest_V'" in w and "SSID stays 'Guest'" in w for w in warns), str(warns))

    # R1's SSID / name rules: SZ allowed "430T " (trailing space) and the
    # create failed twice with a bare 400. Trimmed with a warning; an SSID R1
    # can't take at all stops the plan with the reason.
    from workflow.phases.sz_migration.validate import _r1_ssid, _r1_name_base
    fixed, err, note = _r1_ssid("430T ")
    failures += check("trailing space trimmed from the SSID, said so", fixed == "430T" and not err and "430T " in note,
                      str((fixed, err, note)))
    failures += check("an SSID R1 can't take is an error with the reason",
                      _r1_ssid("A")[1] and _r1_ssid("bad`ssid")[1] and "backtick" in _r1_ssid("bad`ssid")[1])
    failures += check("name edges trimmed, ` and $( removed",
                      _r1_name_base(" 430T ") == "430T" and _r1_name_base("a$(b`c") == "abc")

    out = await run_validate(inv=inventory([{"id": "r1-u101", "name": "unit-101"}]))
    t = out.unit_mappings["w3"].plan.extra["activations"]
    failures += check("existing R1 group of that name reused",
                      t and t[0]["r1_ap_group_id"] == "r1-u101" and not t[0]["r1_ap_group_create"]
                      and out.ap_groups_to_create == [])
    # matched by content: an R1 group carrying exactly Unit101, whatever its name
    carrying = inventory([{"id": "r1-x", "name": "101@Site"}, {"id": "r1-y", "name": "unit-101x"}])
    carrying.wifi_networks = [{"ssid": "Unit101", "venueApGroups": [
        {"venueId": "v", "isAllApGroups": False, "apGroupIds": ["r1-x"]}]}]
    g = build_layout(snapshot(), carrying)["groups"][0]
    failures += check("existing R1 group carrying the same SSIDs reused under its own name",
                      g["exists"] and g["r1_id"] == "r1-x" and g["name"] == "101@Site" and g["matched_by"] == "SSIDs",
                      str(g))
    carrying.wifi_networks[0]["venueApGroups"][0]["apGroupIds"] = ["r1-x", "r1-unlisted"]
    lay_t = build_layout(snapshot(), carrying)
    failures += check("activations on groups the snapshot doesn't list block (incomplete capture)",
                      lay_t["blocked"] and any("recapture" in w["message"] for w in lay_t["warnings"]))

    acts = [a for u in out.unit_mappings.values() for a in u.plan.extra["activations"]]
    failures += check("no SZ AP Group id is ever planned as an R1 id",
                      all(a.get("r1_ap_group_id") not in (SZ_DEFAULT_ID, SZ_AG3_ID) for a in acts))
    ex = out.unit_mappings["w1"].plan.extra
    failures += check("Guest planned for All AP Groups, WPA2 -> 2.4 + 5 GHz only",
                      ex["activation_mode"] == "all_ap_groups" and ex["all_ap_radio_types"] == ["2.4-GHz", "5-GHz"],
                      str(ex.get("all_ap_radio_types")))
    failures += check("WPA3 WLAN keeps 6 GHz",
                      "6-GHz" in out.unit_mappings["w2"].plan.extra["all_ap_radio_types"])
    warns = [c.description for c in out.validation_result.conflicts if c.severity == "warning"]
    failures += check("dropping 6 GHz is reported", any("6 GHz" in w and "Guest" in w for w in warns))

    # 3 (un-broadcast, limit)
    failures += check("WLAN nothing broadcasts: not a unit", "w4" not in out.unit_mappings
                      and [u["id"] for u in lay["unbroadcast"]] == ["w4"])
    out = await run_validate({"layout_choices": {"include_unbroadcast": ["w4"]}})
    failures += check("ticked: created, not activated",
                      out.unit_mappings["w4"].plan.extra["activation_mode"] == "none")
    big = snapshot()
    extra = [SZWLANFull(id=f"x{i}", name=f"X{i}", ssid=f"X{i}", auth_type="WPA2-PSK") for i in range(14)]
    big.wlans += extra
    big.wlan_groups[0].members += [SZWLANGroupMember(id=w.id, name=w.name, ssid=w.ssid) for w in extra]
    lay = build_layout(big, inventory())
    failures += check("16 SSIDs on All AP Groups blocks",
                      lay["blocked"] and any(w["severity"] == "error" for w in lay["warnings"]))

    # 3b
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
    failures += check("venue settings are their own stage, not part of this job",
                      "apply_venue_settings" not in deps and deps["create_radius_profiles"] == ["sz_validate_and_plan"])

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

    # 10 All AP Groups
    out, calls = await run_activate([], {}, mode="all_ap_groups", dpsk_pool_id="pool-1")
    failures += check("venue-wide activation, radios + DPSK pool sent, read back",
                      out.activated and len(calls) == 1 and calls[0]["venue_wide"]
                      and calls[0]["radio_types"] == ["2.4-GHz", "5-GHz"] and calls[0]["dpsk_service_id"] == "pool-1")
    out, calls = await run_activate([], {"venue_wide": True}, mode="all_ap_groups")
    failures += check("already on All AP Groups -> no call", not calls and out.already_active == 1)
    msg = await raises(run_activate([], {"bound": {"g1"}}, mode="all_ap_groups"))
    failures += check("already on specific groups -> not widened", msg is not None and "specific" in msg, str(msg))

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
