#!/usr/bin/env python3
"""
WLAN Activator: suggestions, plan, and direct AP Group activation.

WHAT THIS GUARDS

  1. Suggestions, per unit: units come from networks fitting
     {unit}@{property}, band variants (907-2.4) join their base unit and
     get only their band's radio; a unit's groups are where its WLAN is
     active (several allowed), else one named for it; units with no AP
     count; an AP takes the longest unit token in its name ("1-101" over
     "101", never "101" in "1101"), ties are ambiguous; an AP in another
     group that carries its WLAN is not moved; a WLAN active only in
     another venue says so.
  2. Plan: WLAN resolves by name then SSID, a shared SSID is not guessed;
     unmatched APs and unresolved WLANs are warnings; a DPSK network with no
     DPSK service and a group over 15 SSIDs are blocking errors; a WLAN
     already bound to its existing group is not re-activated; a venue-wide
     WLAN is a warning; a blank AP still creates + activates.
  3. Activation: step 1 runs ONCE per network even when two groups run in
     parallel and the query index lags the write; a venue-wide network's
     step 1 goes to /settings; a network already on the venue skips step 1;
     the DPSK pool and radios ride along; read-back failure fails the unit.
  4. Graph: create_ap_group -> activate_wlans -> assign_aps.
  5. Done/todo + VLANs: property-wide WLANs (bound to most groups) are
     detected and expected on every group; a row is done only when the AP
     is in its group and every WLAN is bound; an AP named like a group goes
     there; a VLAN is the group override (new activation carries it, a
     bound pair with a different one gets step 3 only, an equal one is left
     alone, a wrong one fails read-back); radios copy an existing binding.

Usage:
    docker compose exec backend python scripts/test_wlan_activator.py

Exits non-zero on failure.
"""

import asyncio
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.wlan_activator import build_suggestions, unit_for_name, radio_types_for
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.validate_wlan_activator as validate_mod
import workflow.phases.activate_wlans as activate_mod
from workflow.workflows.wlan_activator import WlanActivatorWorkflow

FAILS = 0


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


def net(nid, name, ssid=None, sub="psk", sec="WPA2", vags=None):
    return {"id": nid, "name": name, "ssid": ssid or name, "nwSubType": sub,
            "securityProtocol": sec, "venueApGroups": vags or []}


# ============================================================================
# 1. Suggestions
# ============================================================================

def test_suggestions():
    print("\n1. Suggestions")
    check("token: 101 in '101-R550'", unit_for_name("101-R550", ["101"])[0] == "101")
    check("token: 101 in 'Unit101'", unit_for_name("Unit101", ["101"])[0] == "101")
    check("token: 101 not in '1101-R550'", unit_for_name("1101-R550", ["101"])[0] is None)
    check("longest wins: 1-101 over 101", unit_for_name("1-101-R550", ["101", "1-101"])[0] == "1-101")
    u, tied = unit_for_name("101-102-AP", ["101", "102"])
    check("equal-length tie is ambiguous", u is None and tied == ["101", "102"], f"{u} {tied}")

    bound = lambda *g: [{"venueId": "v", "isAllApGroups": False, "apGroupIds": list(g)}]
    networks = [
        net("n101", "101@Fieldhouse"),
        net("n102", "102@fieldhouse"),                       # case-insensitive pattern
        net("n103", "Res-103", ssid="103@Fieldhouse", vags=bound("g103x")),   # matched by SSID
        net("n104", "104@Fieldhouse"),                       # no AP
        net("n907", "907@Fieldhouse", vags=bound("g907")),
        net("n907a", "907-2.4@Fieldhouse"), net("n907b", "907-5@Fieldhouse"),
        net("n2524", "2524@Fieldhouse", vags=bound("g2524a", "g2524b")),
        net("n300", "300@Fieldhouse", vags=[{"venueId": "other", "isAllApGroups": True}]),
        net("nX", "Guest"),
    ]
    aps = [
        {"serialNumber": "S1", "name": "101-R550", "apGroupId": "g0"},
        {"serialNumber": "S2", "name": "Unit 102 Living", "apGroupId": "g0"},
        {"serialNumber": "S3", "name": "Hallway", "apGroupId": "g103x"},  # current group carries 103
        {"serialNumber": "S4", "name": "Lobby", "apGroupId": "g0"},
        {"serialNumber": "S5", "name": "101-102 shared", "apGroupId": "g0"},
        {"serialNumber": "S6", "name": "2524_A", "apGroupId": "g2524a"},
        {"serialNumber": "S7", "name": "2524_B", "apGroupId": "g2524b"},
        {"serialNumber": "S8", "name": "101*", "apGroupId": "g101star"},  # alt group carrying 101
    ]
    groups = [{"id": "g0", "name": ""}, {"id": "g103x", "name": "103_X"}, {"id": "g907", "name": "907_SER"},
              {"id": "g2524a", "name": "2524_A"}, {"id": "g2524b", "name": "2524_B"},
              {"id": "g101star", "name": "101*"}, {"id": "g101", "name": "101"}]
    networks[0]["venueApGroups"] = bound("g101", "g101star")
    s = build_suggestions(aps, groups, networks, venue_id="v", property_name="Fieldhouse", extra_wlans=[])
    U = {u["unit"]: u for u in s["units"]}
    check("units from the pattern", set(U) == {"101", "102", "103", "104", "907", "2524", "300"}, sorted(U))
    check("pattern literal is case-insensitive", U["102"]["wlans"] == ["102@fieldhouse"])
    check("SSID match gives the network NAME", U["103"]["wlans"] == ["Res-103"])
    check("band variants join the base unit", U["907"]["wlans"] == ["907@Fieldhouse", "907-2.4@Fieldhouse", "907-5@Fieldhouse"],
          U["907"]["wlans"])
    check("band unit's group = where its WLAN is active", U["907"]["ap_groups"] == ["907_SER"])
    check("band variants need activating", U["907"]["todo"] == ["activate 907-2.4@Fieldhouse", "activate 907-5@Fieldhouse"],
          U["907"]["todo"])
    check("unit with no AP still counted", U["104"]["aps"] == [] and not U["104"]["done"])
    check("unit spans every group its WLAN is on", U["2524"]["ap_groups"] == ["2524_A", "2524_B"] and U["2524"]["done"],
          U["2524"])
    check("AP by name -> unit, needs moving", [a["move"] for a in U["102"]["aps"]] == [True])
    a101 = {a["ap_name"]: a for a in U["101"]["aps"]}
    check("AP in another group carrying the WLAN is not moved", not a101["101*"]["move"], a101["101*"])
    check("that group counts as the unit's", "101*" in U["101"]["ap_groups"], U["101"]["ap_groups"])
    check("AP elsewhere is moved", a101["101-R550"]["move"])
    check("current-group fallback", [a["ap_name"] for a in U["103"]["aps"]] == ["Hallway"] and U["103"]["done"], U["103"])
    check("new group flagged", "create AP Group 102" in U["102"]["todo"], U["102"]["todo"])
    check("active in another venue is said", any("other venue" in t for t in U["300"]["todo"]), U["300"]["todo"])
    other = {a["ap_name"]: a for a in s["other_aps"]}
    check("no unit -> no_match", other["Lobby"]["status"] == "no_match")
    check("two units -> ambiguous", other["101-102 shared"]["status"] == "ambiguous")
    check("band WLAN radios = its band", radio_types_for(networks[5]) == ["2.4-GHz"]
          and radio_types_for(networks[6]) == ["5-GHz"])
    s2 = build_suggestions(aps, groups, networks, venue_id="v", group_pattern="Unit-{unit}", property_name="Fieldhouse")
    check("group pattern applied", {u["unit"]: u for u in s2["units"]}["102"]["ap_groups"] == ["Unit-102"])


# ============================================================================
# Fake R1
# ============================================================================

class World:
    """Networks by id. Reads can lag writes by `lag` reads per network."""

    def __init__(self, networks, lag=0):
        self.truth = {n["id"]: n for n in networks}
        self.visible = copy.deepcopy(self.truth)
        self.lag = lag
        self.stale = {}
        self.calls = []
        self.settings = {}       # (net, group) -> {"radioTypes", "vlanId"?}

    def write(self, nid):
        self.stale[nid] = self.lag

    def read(self, nid):
        if self.stale.get(nid, 0) > 0:
            self.stale[nid] -= 1
        else:
            self.visible[nid] = copy.deepcopy(self.truth[nid])
        return copy.deepcopy(self.visible.get(nid, {}))

    def vag(self, nid, venue):
        for v in self.truth[nid]["venueApGroups"]:
            if v["venueId"] == venue:
                return v
        return None


class FakeVenues:
    def __init__(self, w, aps=None, groups=None):
        self.w, self.aps, self.groups = w, aps or [], groups or []

    async def get_aps_by_tenant_venue(self, t, v):
        return {"data": self.aps}

    async def list_ap_groups_in_venue(self, t, v):
        return self.groups

    @staticmethod
    def index_ap_groups_by_name(groups):
        return {g["name"]: g["id"] for g in groups if g.get("name")}

    async def activate_ssid_for_ap_group_direct(self, tenant_id, venue_id, wifi_network_id, ap_group_id,
                                                radio_types=None, vlan_id=None, dpsk_service_id=None,
                                                already_activated=False, wait_for_completion=True,
                                                step1_ap_groups=None, skip_step1=False):
        await asyncio.sleep(0)   # let a parallel unit interleave
        self.w.calls.append(dict(net=wifi_network_id, group=ap_group_id, step1=not skip_step1,
                                 settings=already_activated, radios=radio_types, pool=dpsk_service_id,
                                 vlan=vlan_id))
        n = self.w.truth[wifi_network_id]
        vag = self.w.vag(wifi_network_id, venue_id)
        if not skip_step1:
            if already_activated:
                if not vag:
                    raise RuntimeError("not activated on the venue")
                vag.update(isAllApGroups=False, apGroupIds=[ap_group_id])     # /settings REPLACES
            else:
                if vag:
                    raise RuntimeError("already activated on the venue")
                n["venueApGroups"].append({"venueId": venue_id, "isAllApGroups": False, "apGroupIds": [ap_group_id]})
            vag = self.w.vag(wifi_network_id, venue_id)
        if not vag or vag.get("isAllApGroups"):
            raise RuntimeError("Wi-Fi network is not activated on the venue")
        if ap_group_id not in vag["apGroupIds"]:
            vag["apGroupIds"].append(ap_group_id)
        st = {"radioTypes": radio_types or ["2.4-GHz", "5-GHz"]}
        if vlan_id is not None:
            st["vlanId"] = vlan_id
        self.w.settings[(wifi_network_id, ap_group_id)] = st
        self.w.write(wifi_network_id)

    async def get_ssid_ap_group_settings(self, t, v, nid, gid):
        return dict(self.w.settings.get((nid, gid), {"radioTypes": ["2.4-GHz", "5-GHz"]}))

    async def set_ssid_ap_group_settings(self, tenant_id, venue_id, wifi_network_id, ap_group_id,
                                         radio_types, vlan_id=None, wait_for_completion=True):
        self.w.calls.append(dict(net=wifi_network_id, group=ap_group_id, step3_only=True,
                                 radios=radio_types, vlan=vlan_id))
        st = {"radioTypes": radio_types}
        if vlan_id is not None:
            st["vlanId"] = vlan_id
        self.w.settings[(wifi_network_id, ap_group_id)] = st


class FakeNetworks:
    def __init__(self, w, pools=None):
        self.w, self.pools = w, pools or {}

    async def query_wifi_network_by_id(self, nid, t=None):
        return self.w.read(nid)

    async def get_wifi_networks(self, t, venue_id=None):
        return {"data": [copy.deepcopy(n) for n in self.w.truth.values()]}

    async def get_dpsk_services_on_network(self, nid, t=None):
        return {"data": [{"id": self.pools[nid]}] if nid in self.pools else []}


def ctx(w, **kw):
    r1 = SimpleNamespace(venues=FakeVenues(w, kw.get("aps"), kw.get("groups")),
                         networks=FakeNetworks(w, kw.get("pools")))
    return PhaseContext(job_id=kw.get("job", "j"), r1_client=r1, venue_id="v", tenant_id="t", options={})


# ============================================================================
# 2. Plan
# ============================================================================

def test_plan():
    print("\n2. Plan")
    networks = [
        net("n101", "101@F"),
        net("n102", "102@F", sub="dpsk"),                      # DPSK, pool linked
        net("n103", "103@F", sub="DPSK"),                      # DPSK, no pool -> error
        net("n104", "104@F", vags=[{"venueId": "v", "isAllApGroups": False, "apGroupIds": ["g104"]}]),
        net("n105", "105@F", vags=[{"venueId": "v", "isAllApGroups": True}]),
        net("dupA", "Dup-A", ssid="Shared"), net("dupB", "Dup-B", ssid="Shared"),
    ]
    aps = [{"serialNumber": f"S{i}", "name": f"{i}-AP"} for i in (101, 102, 103, 104, 105)]
    groups = [{"id": "g104", "name": "104"}]
    rows = [
        {"ap_identifier": "101-AP", "ap_group_name": "101", "wlan": "101@F"},
        {"ap_identifier": "S102", "ap_group_name": "102", "wlan": "102@F"},
        {"ap_identifier": "103-AP", "ap_group_name": "103", "wlan": "103@F"},
        {"ap_identifier": "104-AP", "ap_group_name": "104", "wlan": "104@F"},
        {"ap_identifier": "105-AP", "ap_group_name": "105", "wlan": "105@F"},
        {"ap_identifier": "", "ap_group_name": "106", "wlan": "101@F"},          # no AP
        {"ap_identifier": "ghost", "ap_group_name": "101", "wlan": "101@F"},
        {"ap_identifier": "101-AP", "ap_group_name": "999", "wlan": "101@F"},    # moved twice
        {"ap_identifier": "x", "ap_group_name": "107", "wlan": "Shared"},
        {"ap_identifier": "x", "ap_group_name": "108", "wlan": "Nope"},
    ]
    w = World(networks)
    phase = validate_mod.ValidateWlanActivatorPhase(ctx(w, aps=aps, groups=groups, pools={"n102": "pool-102"}))
    out = asyncio.run(phase.execute(phase.Inputs(activations=rows)))
    units = out.unit_mappings
    vr = out.validation_result
    cf = {(c.resource_name, c.severity) for c in vr.conflicts}
    descs = " | ".join(c.description for c in vr.conflicts)

    check("one unit per group", set(units) == {f"apgroup_{g}" for g in ("101", "102", "103", "104", "105", "106", "999")},
          sorted(units))
    check("shared SSID not guessed", any(n == "Shared" and s == "warning" for n, s in cf))
    check("unknown WLAN is a warning", ("Nope", "warning") in cf)
    check("unmatched AP reported", out.unmatched_identifiers == ["ghost"], out.unmatched_identifiers)
    check("AP listed twice: first row wins", units["apgroup_101"].plan.ap_serial_numbers == ["S101"]
          and units["apgroup_999"].plan.ap_serial_numbers == [] and "FIRST" in descs)
    check("DPSK with no service blocks", ("103@F", "error") in cf and not vr.valid)
    w102 = units["apgroup_102"].input_config["wlans"][0]
    check("DPSK pool carried", w102["dpsk_pool_id"] == "pool-102", w102)
    check("already bound -> nothing to activate", units["apgroup_104"].input_config["wlans"] == [])
    check("existing group pre-resolved", units["apgroup_104"].resolved.ap_group_id == "g104")
    check("venue-wide WLAN warned", "All AP Groups" in descs)
    check("blank AP still activates", units["apgroup_106"].plan.ap_serial_numbers == []
          and units["apgroup_106"].input_config["wlans"][0]["network_id"] == "n101")

    # 15-SSID ceiling: 14 venue-wide SSIDs + 2 new on one group.
    many = [net(f"vw{i}", f"VW{i}", vags=[{"venueId": "v", "isAllApGroups": True}]) for i in range(14)]
    many += [net("a", "A@F"), net("b", "B@F")]
    w = World(many)
    phase = validate_mod.ValidateWlanActivatorPhase(ctx(w, aps=[], groups=[]))
    out = asyncio.run(phase.execute(phase.Inputs(activations=[
        {"ap_group_name": "G", "wlan": "A@F"}, {"ap_group_name": "G", "wlan": "B@F"}])))
    check("over 15 SSIDs blocks", not out.validation_result.valid
          and any("16 SSIDs" in c.description for c in out.validation_result.conflicts))


# ============================================================================
# 3. Activation
# ============================================================================

def run_activate(w, unit, group_id, wlans, job="j", pools=None):
    phase = activate_mod.ActivateWlansPhase(ctx(w, job=job, pools=pools))
    return phase.execute(phase.Inputs(unit_id=f"apgroup_{unit}", unit_number=unit, ap_group_id=group_id,
                                      ap_group_name=unit, wlans=wlans))


def test_activation():
    print("\n3. Activation")

    activate_mod.READBACK_RETRY_DELAYS = [0, 0]

    # One new network on two groups, in parallel, with reads lagging writes.
    w = World([net("n1", "Shared@F", sec="WPA3")], lag=1)
    wl = [{"network_id": "n1", "name": "Shared@F", "ssid": "Shared@F", "radio_types": radio_types_for(w.truth["n1"]),
           "dpsk_pool_id": None}]

    async def both():
        return await asyncio.gather(run_activate(w, "A", "gA", wl, job="j1"), run_activate(w, "B", "gB", wl, job="j1"))
    asyncio.run(both())
    step1 = [c for c in w.calls if c["step1"]]
    check("step 1 once for a shared network", len(step1) == 1, w.calls)
    check("both groups bound", set(w.vag("n1", "v")["apGroupIds"]) == {"gA", "gB"}, w.vag("n1", "v"))
    # Deterministic lag: A is done; B's read still shows the network off the
    # venue. Only the job's own record of step 1 keeps B from re-sending it.
    w = World([net("n7", "Lag@F")])
    pre = copy.deepcopy(w.truth)
    wl7 = [{"network_id": "n7", "name": "Lag@F"}]
    asyncio.run(run_activate(w, "A", "gA", wl7, job="j7"))
    w.visible, w.stale = pre, {"n7": 1}
    try:
        asyncio.run(run_activate(w, "B", "gB", wl7, job="j7"))
    except RuntimeError as e:
        print(f"        B failed: {e}")
    check("stale read after step 1 does not re-send it",
          [c["step1"] for c in w.calls] == [True, False], w.calls)
    check("6 GHz for WPA3", "6-GHz" in (wl[0]["radio_types"] or []))
    check("VLAN inherited (none sent)", all(c["vlan"] is None for c in w.calls))

    # Venue-wide network: step 1 to /settings, ends specific.
    w = World([net("n2", "X@F", vags=[{"venueId": "v", "isAllApGroups": True}])])
    asyncio.run(run_activate(w, "A", "gA", [{"network_id": "n2", "name": "X@F"}], job="j2"))
    check("venue-wide -> /settings step 1", w.calls[0]["step1"] and w.calls[0]["settings"], w.calls)
    check("venue-wide -> specific", w.vag("n2", "v")["isAllApGroups"] is False)

    # Already on another group in the venue: steps 2-3 only, existing group kept.
    w = World([net("n3", "Y@F", sub="dpsk", vags=[{"venueId": "v", "isAllApGroups": False, "apGroupIds": ["gOld"]}])])
    asyncio.run(run_activate(w, "A", "gA", [{"network_id": "n3", "name": "Y@F", "dpsk_pool_id": "p3"}], job="j3"))
    check("associated network skips step 1", not w.calls[0]["step1"])
    check("existing binding kept", set(w.vag("n3", "v")["apGroupIds"]) == {"gOld", "gA"})

    # Already bound to this group: no PUTs.
    w = World([net("n4", "Z@F", vags=[{"venueId": "v", "isAllApGroups": False, "apGroupIds": ["gA"]}])])
    out = asyncio.run(run_activate(w, "A", "gA", [{"network_id": "n4", "name": "Z@F"}], job="j4"))
    check("already bound -> no calls", w.calls == [] and out.wlans_already_active == 1)

    # DPSK pool and default radios ride on a fresh activation.
    w = World([net("n5", "D@F", sub="dpsk")])
    asyncio.run(run_activate(w, "A", "gA", [{"network_id": "n5", "name": "D@F", "dpsk_pool_id": "p5",
                                              "radio_types": radio_types_for(w.truth["n5"])}], job="j5"))
    check("DPSK pool on step 1", w.calls[0]["pool"] == "p5")
    check("2.4 + 5 for WPA2", w.calls[0]["radios"] == ["2.4-GHz", "5-GHz"])

    # Activation that R1 never records fails the unit.
    w = World([net("n6", "F@F")])

    async def silently_dropped(**kw):
        w.calls.append(kw)
    venues = FakeVenues(w)
    venues.activate_ssid_for_ap_group_direct = silently_dropped
    phase = activate_mod.ActivateWlansPhase(PhaseContext(
        job_id="j6", r1_client=SimpleNamespace(venues=venues, networks=FakeNetworks(w)),
        venue_id="v", tenant_id="t", options={}))
    try:
        asyncio.run(phase.execute(phase.Inputs(unit_id="u", unit_number="A", ap_group_id="gA",
                                               ap_group_name="A", wlans=[{"network_id": "n6", "name": "F@F"}])))
        check("read-back failure fails the unit", False, "no exception")
    except RuntimeError as e:
        check("read-back failure fails the unit", "Not active" in str(e), str(e))



# ============================================================================
# 4. Graph
# ============================================================================

def test_graph():
    print("\n4. Graph")
    deps = {p.id: p.depends_on for p in WlanActivatorWorkflow.phases}
    # Plus link_dpsk_pools directly: create_ap_group is pre-completed for existing groups,
    # and the scheduler checks direct dependencies only.
    check("activate after create", deps["activate_wlans"] == ["create_ap_group", "link_dpsk_pools"])
    check("assign after activate", deps["assign_aps"] == ["activate_wlans"])
    from workflow.workflows import get_workflow
    from workflow.phases.registry import get_phase_class
    check("workflow registered", get_workflow("wlan_activator") is WlanActivatorWorkflow)
    check("phases registered", all(get_phase_class(p) for p in ("validate_wlan_activator", "activate_wlans")))


# ============================================================================
# 5. Done/todo and VLANs
# ============================================================================

def test_done_and_vlans():
    print("\n5. Done/todo and VLANs")
    bound = lambda *g: [{"venueId": "v", "isAllApGroups": False, "apGroupIds": list(g)}]
    groups = [{"id": f"g{u}", "name": u} for u in ("101", "102", "103", "Clubhouse")]
    networks = [
        net("n101", "101@F", vags=bound("g101")),
        net("n102", "102@F"),                                           # not active anywhere
        net("n103", "103@F", vags=[{"venueId": "v", "isAllApGroups": True}]),
        net("pw", "Resident WiFi", sec="WPA2Personal", vags=bound("g101", "g102", "g103")),
        net("vid", "video", vags=bound("g101", "g102", "g103", "gClubhouse")),
        net("odd", "Lobby Only", vags=bound("g101")),                   # one group: not property-wide
    ]
    aps = [
        {"serialNumber": "A1", "name": "101-AP", "apGroupId": "g101"},
        {"serialNumber": "A2", "name": "102-AP", "apGroupId": "g101"},
        {"serialNumber": "A3", "name": "103-AP", "apGroupId": "g103"},
        {"serialNumber": "A4", "name": "Clubhouse", "apGroupId": ""},
    ]
    s = build_suggestions(aps, groups, networks, venue_id="v", property_name="F")
    U = {u["unit"]: u for u in s["units"]}
    other = {a["ap_name"]: a for a in s["other_aps"]}
    check("property-wide WLANs detected", [w["name"] for w in s["property_wlans"]] == ["video", "Resident WiFi"],
          s["property_wlans"])
    check("fully in place -> done", U["101"]["done"], U["101"])
    check("todo lists move + missing WLANs", not U["102"]["done"]
          and any(t.startswith("move 102-AP") for t in U["102"]["todo"])
          and "activate 102@F" in U["102"]["todo"], U["102"]["todo"])
    check("venue-wide unit WLAN is not done", any("All AP Groups" in t for t in U["103"]["todo"]), U["103"]["todo"])
    check("AP named like a group -> group_only", other["Clubhouse"]["status"] == "group_only"
          and other["Clubhouse"]["ap_group"] == "Clubhouse")
    check("group_only still expects property WLANs", "activate Resident WiFi" in other["Clubhouse"]["todo"],
          other["Clubhouse"]["todo"])
    check("done/todo counts", s["counts"]["units_done"] == 1 and s["counts"]["units_todo"] == 2, s["counts"])
    s2 = build_suggestions(aps, groups, networks, venue_id="v", property_name="F", extra_wlans=[])
    check("no extras -> group_only AP only needs the move",
          {a["ap_name"]: a for a in s2["other_aps"]}["Clubhouse"]["todo"] == ["move AP (now in default)"])

    # Plan: VLAN parsing, radios copied, VLAN change on a bound pair.
    w = World(networks)
    w.settings[("vid", "g101")] = {"radioTypes": ["2.4-GHz", "5-GHz", "6-GHz"]}
    w.settings[("pw", "g102")] = {"radioTypes": ["2.4-GHz", "5-GHz"], "vlanId": 50}
    phase = validate_mod.ValidateWlanActivatorPhase(ctx(w, aps=aps, groups=groups))
    out = asyncio.run(phase.execute(phase.Inputs(activations=[
        {"ap_identifier": "", "ap_group_name": "NEW", "wlan": "video", "vlan": ""},
        {"ap_identifier": "", "ap_group_name": "NEW", "wlan": "Resident WiFi", "vlan": "77"},
        {"ap_identifier": "", "ap_group_name": "102", "wlan": "Resident WiFi", "vlan": "60"},   # 50 -> 60
        {"ap_identifier": "", "ap_group_name": "101", "wlan": "Resident WiFi", "vlan": ""},    # bound, inherit
        {"ap_identifier": "", "ap_group_name": "103", "wlan": "Resident WiFi", "vlan": "abc"},
    ])))
    u = out.unit_mappings
    new = {x["name"]: x for x in u["apgroup_NEW"].input_config["wlans"]}
    check("radios copied from an existing binding (6 GHz kept)", new["video"]["radio_types"][-1] == "6-GHz",
          new["video"])
    check("VLAN carried on a new activation", new["Resident WiFi"]["vlan"] == 77 and new["video"]["vlan"] is None)
    w102 = u["apgroup_102"].input_config["wlans"]
    check("bound pair with a different VLAN is planned", len(w102) == 1 and w102[0]["vlan"] == 60, w102)
    check("bound pair, no VLAN -> skipped", u["apgroup_101"].input_config["wlans"] == [])
    check("bad VLAN warned, inherits", any("Not a VLAN" in c.description for c in out.validation_result.conflicts)
          and u["apgroup_103"].input_config["wlans"] == [])

    # Activation: VLAN change on a bound pair is step 3 only, radios kept.
    w.calls.clear()
    asyncio.run(run_activate(w, "102", "g102", w102, job="j8"))
    check("VLAN change = step 3 only", len(w.calls) == 1 and w.calls[0].get("step3_only") and w.calls[0]["vlan"] == 60,
          w.calls)
    check("VLAN change keeps radios", w.calls[0]["radios"] == ["2.4-GHz", "5-GHz"])
    w.calls.clear()
    asyncio.run(run_activate(w, "102", "g102", w102, job="j8"))
    check("equal VLAN -> no calls", w.calls == [])

    # New activation carries the VLAN through.
    w.calls.clear()
    asyncio.run(run_activate(w, "102", "g102", [{"network_id": "n102", "name": "102@F", "vlan": 2397}], job="j9"))
    check("new activation sends VLAN", w.calls[0]["vlan"] == 2397 and w.settings[("n102", "g102")]["vlanId"] == 2397)

    # Read-back catches a VLAN R1 did not keep.
    async def drop_vlan(**kw):
        w.calls.append(kw)
    venues = FakeVenues(w)
    venues.set_ssid_ap_group_settings = drop_vlan
    phase = activate_mod.ActivateWlansPhase(PhaseContext(
        job_id="j10", r1_client=SimpleNamespace(venues=venues, networks=FakeNetworks(w)),
        venue_id="v", tenant_id="t", options={}))
    try:
        asyncio.run(phase.execute(phase.Inputs(unit_id="u", unit_number="101", ap_group_id="g101",
                                               ap_group_name="101",
                                               wlans=[{"network_id": "pw", "name": "Resident WiFi", "vlan": 99}])))
        check("VLAN read-back failure fails the unit", False, "no exception")
    except RuntimeError as e:
        check("VLAN read-back failure fails the unit", "wanted 99" in str(e), str(e))


if __name__ == "__main__":
    test_suggestions()
    test_plan()
    test_activation()
    test_graph()
    test_done_and_vlans()
    print(f"\n{'ALL PASS' if not FAILS else f'{FAILS} FAILURE(S)'}")
    sys.exit(1 if FAILS else 0)
