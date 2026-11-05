#!/usr/bin/env python3
"""
SZ -> R1 migration, AP Mapping stage.

Every AP should broadcast in R1 what it broadcasts in SZ: AP Groups from the
SZ broadcast sets, each AP in its group, each WLAN on its groups with THAT
group's radios, checked by the audit's per-AP broadcast comparison.

WHAT THIS GUARDS

  1. Radios per group: a WLAN on 5 GHz only at one AP's group and on
     2.4+5 at another's is activated with each group's own radios.
  2. Plan, per SZ AP: move (in another R1 group) / add (not in the venue) /
     already / unmapped (group neither in R1 nor ticked) / blocked (no
     serial); APs broadcasting only the All AP Groups set target the
     venue's default group -- unless R1 has them in a group with SSIDs of
     its own (a fix made in R1): kept, not moved.
  3. Plan, per WLAN: already / needed against R1's own venueApGroups; a
     group still to be created is "awaiting_group"; un-ticked groups are
     reported as skipped; groups_to_create lists the ticked ones.
  4. ap_group_ops.place_aps (shared with APG Cleanup): adds POST
     {name, serialNumber} into the group, moves PUT .../aps/{serial}; each
     row ends added / moved / failed / unconfirmed from R1's AP list, not
     from the task answers.
  5. map_aps maps what it can and fails (non-critical) on what it can't.
  6. (Activation is Create WLANs' job -- covered by test_migration_activation.)
  7. Graph: APs only -- plan -> map -> verify. A group the APs go into but
     R1 lacks (manual, by name, ticked but not built) is created by map_aps,
     empty, before the APs are placed (group_create / create_groups).
 10. By name: the unit anywhere in the name as a token ("*20102-NEW"), and
     derived groups Create WLANs didn't build are candidates (created here).
 13. Activate WLANs no AP broadcasts: per-unit ones on their unit's group
     (paired staged group, derived / R1 group of that name, else a new empty
     one), no unit -> All AP Groups; AP Mapping's expected broadcast follows.
 14. Verify: an AP that only gains WLANs activated on purpose isn't a difference.

Usage:
    docker compose exec backend python scripts/test_ap_mapping.py

Exits non-zero on failure.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import (
    SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZWLANGroup, SZWLANGroupMember, SZAPGroupEnriched,
)
from schemas.r1_inventory import R1VenueInventory
from services import ap_group_ops
from services.sz_migration.ap_mapping import plan_ap_mapping
from services.sz_migration.r1_layout import build_layout, group_key, wlan_activations
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.ap_mapping as phases
from workflow.workflows.sz_ap_mapping import SZApMappingWorkflow


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def snapshot():
    """10 APs on the zone default (Guest, Secure6 -> All AP Groups). AP0 overrides to unit-101
    (+Unit101), AP1 to unit-102 on 5 GHz only (+Unit102), AP2 to unit-103 (+Unit101, Unit102).
    AP9 has no serial."""
    wlans = [SZWLANFull(id="w1", name="Guest", ssid="Guest", auth_type="WPA2-PSK", encryption={"method": "WPA2"}),
             SZWLANFull(id="w2", name="Secure6", ssid="Secure6", auth_type="WPA3-SAE", encryption={"method": "WPA3"}),
             SZWLANFull(id="w3", name="Unit101", ssid="Unit101", auth_type="WPA2-PSK", encryption={"method": "WPA2"}),
             SZWLANFull(id="w5", name="Unit102", ssid="Unit102", auth_type="WPA2-PSK", encryption={"method": "WPA2"}),
             # Not broadcast: unit WLAN Groups named after APs, overrides backed out (Murabella)
             SZWLANFull(id="w6", name="Unit106", ssid="Unit106", auth_type="WPA2-PSK", encryption={"method": "WPA2"}),
             SZWLANFull(id="w6b", name="Unit106-IoT", ssid="Unit106-IoT", auth_type="WPA2-PSK", encryption={"method": "WPA2"}),
             SZWLANFull(id="w7", name="Unit107", ssid="Unit107", auth_type="WPA2-PSK", encryption={"method": "WPA2"}),
             SZWLANFull(id="w8", name="Orphan", ssid="Orphan", auth_type="WPA2-PSK", encryption={"method": "WPA2"}),
             SZWLANFull(id="w9", name="Unit100", ssid="Unit100", auth_type="WPA2-PSK", encryption={"method": "WPA2"})]
    m = {w.id: SZWLANGroupMember(id=w.id, name=w.name, ssid=w.ssid) for w in wlans}
    aps = [{"apMac": f"ap{i}", "deviceName": f"AP-{i}", "serial": (f"S{i}" if i != 9 else None),
            "apGroupId": "sz-default"} for i in range(10)]

    def radios(per_band):
        return {b: {"wlan_group_id": per_band.get(b), "service_enabled": True}
                for b in ("radio24g", "radio5g", "radio6g")}
    detail = {f"ap{i}": {"dual5g": False, "radios": radios({})} for i in range(10)}
    detail["ap0"]["radios"] = radios({b: "wg-101" for b in ("radio24g", "radio5g", "radio6g")})
    detail["ap1"]["radios"] = radios({"radio5g": "wg-102"})
    detail["ap2"]["radios"] = radios({b: "wg-103" for b in ("radio24g", "radio5g", "radio6g")})
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone"),
        wlans=wlans,
        wlan_groups=[SZWLANGroup(id="wg-default", name="default", members=[m["w1"], m["w2"]]),
                     SZWLANGroup(id="wg-101", name="unit-101", members=[m["w1"], m["w2"], m["w3"]]),
                     SZWLANGroup(id="wg-102", name="unit-102", members=[m["w1"], m["w2"], m["w5"]]),
                     SZWLANGroup(id="wg-103", name="unit-103", members=[m["w1"], m["w2"], m["w3"], m["w5"]]),
                     SZWLANGroup(id="wg-6", name="AP-6", members=[m["w6"], m["w6b"]]),
                     SZWLANGroup(id="wg-7", name="AP-7", members=[m["w7"]]),
                     SZWLANGroup(id="wg-x", name="nobody", members=[m["w8"]]),
                     SZWLANGroup(id="wg-0", name="AP-0", members=[m["w9"]])],
        ap_groups=[SZAPGroupEnriched(id="sz-default", name="default", ap_count=10)],
        aps=aps,
        ap_radio_config=detail,
    )


def inventory():
    aps = [{"serialNumber": f"S{i}", "name": f"AP-{i}", "apGroupId": "r1-default"} for i in (0, 2, 3, 6, 7, 8)]
    aps.append({"serialNumber": "S4", "name": "AP-4", "apGroupId": "g-old"})
    # AP-5 broadcasts only the All AP Groups set in SZ, but sits in a unit group in R1 (a fix made in R1)
    aps.append({"serialNumber": "S5", "name": "AP-5", "apGroupId": "g101"})
    return R1VenueInventory(
        venue_id="v", venue_name="V", tenant_id="t",
        ap_groups=[{"id": "r1-default", "name": "", "isDefault": True},
                   {"id": "g101", "name": "unit-101"}, {"id": "g-old", "name": "old"}],
        aps=aps,
        wifi_networks=[
            {"id": "n1", "name": "Guest", "ssid": "Guest", "nwSubType": "PSK",
             "venueApGroups": [{"venueId": "v", "isAllApGroups": True}]},
            {"id": "n3", "name": "Unit101", "ssid": "Unit101", "nwSubType": "PSK",
             "venueApGroups": [{"venueId": "v", "isAllApGroups": False, "apGroupIds": ["g101"]}]},
        ],
        tenant_networks=[{"id": "n1", "name": "Guest", "ssid": "Guest"},
                         {"id": "n2", "name": "Secure6", "ssid": "Secure6"},
                         {"id": "n3", "name": "Unit101", "ssid": "Unit101"},
                         {"id": "n5", "name": "Unit102", "ssid": "Unit102", "nwSubType": "DPSK"}],
    )


K102 = group_key(frozenset({"w5"}))
K103 = group_key(frozenset({"w3", "w5"}))
CHOICES = {"create_groups": [K102]}


def test_layout_radios():
    print("\n1. Radios per group")
    f = 0
    layout = build_layout(snapshot(), inventory(), CHOICES)
    g = {x["key"]: x for x in layout["groups"]}
    f += check("unit-102 group: Unit102 on 5 GHz only (its AP broadcasts it there)",
               g[K102]["radio_types"]["w5"] == ["5-GHz"], str(g[K102]["radio_types"]))
    f += check("unit-103 group: Unit102 on 2.4 + 5 GHz",
               g[K103]["radio_types"]["w5"] == ["2.4-GHz", "5-GHz"], str(g[K103]["radio_types"]))
    acts = wlan_activations(layout)
    radios = {t["r1_ap_group_name"]: t["r1_radio_types"] for t in acts["w5"]["targets"]}
    f += check("activation targets carry the group's radios, not the WLAN's union",
               radios == {"unit-102": ["5-GHz"]}, str(acts["w5"]))
    f += check("default group lists its APs (broadcast = All AP Groups set)",
               sorted(a["name"] for a in layout["default_group"]["aps"])
               == sorted(f"AP-{i}" for i in range(3, 10)), str(layout["default_group"]["aps"]))
    return f


def test_plan():
    print("\n2-3. Plan: APs and activations")
    f = 0
    built = inventory()
    built.ap_groups.append({"id": "g102", "name": "unit-102"})   # as after Create WLANs
    default = {a["name"]: a for a in plan_ap_mapping(snapshot(), built, CHOICES)["aps"]}
    f += check("adding APs not in R1 is off by default: AP-1 is listed, not added",
               default["AP-1"]["action"] == "not_in_r1", str(default["AP-1"]))
    opted = {a["name"]: a for a in plan_ap_mapping(snapshot(), built, CHOICES, add_new_aps=True)["aps"]}
    f += check("opted in: AP-1 is added into unit-102",
               (opted["AP-1"]["action"], opted["AP-1"]["group_id"]) == ("add", "g102"), str(opted["AP-1"]))
    plan = plan_ap_mapping(snapshot(), inventory(), CHOICES, add_new_aps=True)
    by = {a["name"]: a for a in plan["aps"]}
    f += check("AP-0 moves into existing unit-101 (matched by its SSIDs)",
               (by["AP-0"]["action"], by["AP-0"]["group_id"]) == ("move", "g101"), str(by["AP-0"]))
    f += check("AP-1's group (ticked) isn't in R1 yet: added, its group created by map_aps first",
               (by["AP-1"]["action"], by["AP-1"]["group_name"], by["AP-1"]["group_create"])
               == ("add", "unit-102", True) and plan["create_groups"] == ["unit-102"], str(by["AP-1"]))
    f += check("AP-2's group not ticked: unmapped", by["AP-2"]["action"] == "unmapped", str(by["AP-2"]))
    f += check("AP-3 already in the default group", (by["AP-3"]["action"], by["AP-3"]["group_id"])
               == ("already", "r1-default"), str(by["AP-3"]))
    f += check("AP-5 in unit-101 (SSIDs of its own) is kept, not moved to default",
               (by["AP-5"]["action"], by["AP-5"]["extra_ssids"]) == ("keep", ["Unit101"]), str(by["AP-5"]))
    f += check("AP-4 in 'old' (no SSIDs of its own) moves to the default group", (by["AP-4"]["action"], by["AP-4"]["group_id"])
               == ("move", "r1-default"), str(by["AP-4"]))
    f += check("AP-9 has no serial: blocked", by["AP-9"]["action"] == "blocked", str(by["AP-9"]))
    f += check("groups_to_create = the ticked one", plan["groups_to_create"] == ["unit-102"],
               str(plan["groups_to_create"]))

    acts = {a["name"]: a for a in plan["activations"]}
    f += check("Guest already on All AP Groups", acts["Guest"]["status"] == "already", str(acts["Guest"]))
    f += check("Secure6 needs All AP Groups", (acts["Secure6"]["mode"], acts["Secure6"]["status"])
               == ("all_ap_groups", "needed"), str(acts["Secure6"]))
    f += check("Unit101 already on unit-101; unit-103 reported as skipped",
               acts["Unit101"]["status"] == "already" and acts["Unit101"]["skipped_groups"] == ["unit-103"],
               str(acts["Unit101"]))
    t = acts["Unit102"]["targets"]
    f += check("Unit102 needed, its group still to be created",
               acts["Unit102"]["status"] == "needed" and [x["status"] for x in t] == ["awaiting_group"]
               and acts["Unit102"]["dpsk"] is True, str(acts["Unit102"]))
    c = plan["counts"]
    f += check("counts", (c["aps_move"], c["aps_group_create"], c["aps_already"], c["aps_keep"], c["aps_unmapped"],
                          c["aps_blocked"], c["wlans_needed"], c["wlans_already"]) == (2, 1, 4, 1, 1, 1, 2, 2), str(c))
    return f


class FakeR1:
    """Records requests; R1's AP list is `where` (serial -> group)."""
    ec_type = "MSP"

    def __init__(self, refuse=(), slow=()):
        self.where, self.requests, self.refuse, self.slow = {}, [], set(refuse), set(slow)

    def _resp(self, serial, group):
        if serial in self.refuse:
            return SimpleNamespace(status_code=422, content=b"x", json=lambda: {"errors": [{"message": "nope"}]},
                                   raise_for_status=lambda: (_ for _ in ()).throw(RuntimeError("422 nope")))
        if serial not in self.slow:
            self.where[serial] = group
        return SimpleNamespace(status_code=202, content=b"x", json=lambda: {"requestId": f"r-{serial}"})

    def post(self, path, payload=None, override_tenant_id=None):
        self.requests.append(("POST", path, payload))
        return self._resp(payload["serialNumber"], path.split("/")[4])

    def put(self, path, override_tenant_id=None, **kw):
        self.requests.append(("PUT", path, None))
        parts = path.split("/")
        return self._resp(parts[6], parts[4])

    async def await_task_completion_bulk(self, ids, **kw):
        return {i: {"success": True} for i in ids}


def test_place_aps():
    print("\n4. Shared AP placement (APG Cleanup + AP Mapping)")
    f = 0
    r1 = FakeR1(refuse={"S3"}, slow={"S4"})
    rows = [{"serial": "S1", "ap_name": "AP-1", "to_group_id": "gA", "to_group_name": "A", "op": "add",
             "description": "near library"},
            {"serial": "S2", "ap_name": "AP-2", "to_group_id": "gB", "to_group_name": "B", "op": "move"},
            {"serial": "S3", "ap_name": "AP-3", "to_group_id": "gB", "to_group_name": "B", "op": "move"},
            {"serial": "S4", "ap_name": "AP-4", "to_group_id": "gB", "to_group_name": "B", "op": "move"}]

    async def read_aps():
        return [{"serialNumber": s, "apGroupId": g} for s, g in r1.where.items()]

    asyncio.run(ap_group_ops.place_aps(r1, "t", "v", rows, read_aps))
    st = {r["serial"]: r["status"] for r in rows}
    f += check("add -> added, move -> moved, refused -> failed, not visible -> unconfirmed",
               st == {"S1": "added", "S2": "moved", "S3": "failed", "S4": "unconfirmed"}, str(st))
    f += check("add is POST into the group with name + serial (+ description)",
               ("POST", "/venues/v/apGroups/gA/aps", {"name": "AP-1", "serialNumber": "S1",
                                                       "description": "near library"}) in r1.requests,
               str(r1.requests))
    f += check("move is PUT .../apGroups/{g}/aps/{serial}",
               ("PUT", "/venues/v/apGroups/gB/aps/S2", None) in r1.requests, str(r1.requests))
    f += check("R1's error message kept", "nope" in (rows[2].get("error") or ""), str(rows[2]))
    return f


def test_name_clash_retry():
    print("\n4b. An add refused for a taken name is retried as <name>_<serial>")
    f = 0

    class ClashR1(FakeR1):
        def post(self, path, payload=None, override_tenant_id=None):
            self.requests.append(("POST", path, payload))
            serial = payload["serialNumber"]
            if payload["name"] == "Hallway":       # taken somewhere else in the tenant
                return SimpleNamespace(status_code=202, content=b"x", json=lambda: {"requestId": f"bad-{serial}"})
            self.where[serial] = path.split("/")[4]
            return SimpleNamespace(status_code=202, content=b"x", json=lambda: {"requestId": f"r-{serial}"})

        async def await_task_completion_bulk(self, ids, **kw):
            return {i: ({"success": False, "error": "Property [name] value [Hallway] already exists."}
                        if i.startswith("bad-") else {"success": True}) for i in ids}

    r1 = ClashR1()
    rows = [{"serial": "962379002827", "ap_name": "Hallway", "to_group_id": "gD", "to_group_name": "(default)",
             "op": "add"}]

    async def read_aps():
        return [{"serialNumber": x, "apGroupId": g} for x, g in r1.where.items()]
    asyncio.run(ap_group_ops.place_aps(r1, "t", "v", rows, read_aps))
    names = [p["name"] for _, _, p in r1.requests]
    f += check("refused 'Hallway' -> retried once as 'Hallway_962379002827', added",
               names == ["Hallway", "Hallway_962379002827"] and rows[0]["status"] == "added"
               and rows[0]["renamed_from"] == "Hallway", str((names, rows[0])))
    return f


def test_pending_tasks():
    print("\n4c. A task still running when the wait ends is not a failure")
    f = 0

    class SlowR1(FakeR1):
        """Tasks never finish in the wait (R1 still IN_PROGRESS); S1 shows up in its group on the 2nd read."""
        reads = 0

        async def await_task_completion_bulk(self, ids, **kw):
            return {i: {"success": False, "pending": True,
                        "error": f"Task {i} did not complete after 300s (R1 still reports it IN_PROGRESS)"}
                    for i in ids}

    r1 = SlowR1(slow={"S1", "S2"})
    rows = [{"serial": "S1", "ap_name": "AP-1", "to_group_id": "gA", "to_group_name": "A", "op": "add",
             "description": "near library"},
            {"serial": "S2", "ap_name": "AP-2", "to_group_id": "gA", "to_group_name": "A", "op": "add"}]

    async def read_aps():
        r1.reads += 1
        if r1.reads >= 2:
            r1.where["S1"] = "gA"
        return [{"serialNumber": s, "apGroupId": g} for s, g in r1.where.items()]

    real_sleep = asyncio.sleep

    async def no_sleep(*_a, **_k):
        await real_sleep(0)
    ap_group_ops.asyncio.sleep, saved = no_sleep, ap_group_ops.asyncio.sleep
    try:
        asyncio.run(ap_group_ops.place_aps(r1, "t", "v", rows, read_aps, confirm_seconds=1))
    finally:
        ap_group_ops.asyncio.sleep = saved
    st = {r["serial"]: r["status"] for r in rows}
    f += check("pending, then seen in its group -> added; pending, never seen -> unconfirmed (not failed)",
               st == {"S1": "added", "S2": "unconfirmed"}, str(st))
    return f


def _phase(cls, plan, r1):
    p = cls(PhaseContext(job_id="j", r1_client=r1, venue_id="v", tenant_id="t", options={}))

    async def fresh(_):
        return snapshot(), inventory(), plan
    p.fresh = fresh
    p.emitted = []

    async def emit(msg, level="info", details=None):
        p.emitted.append((level, msg))
    p.emit = emit
    return p


async def _no_report(*a, **k):
    return None


def test_map_phase():
    print("\n5. map_aps")
    f = 0
    phases.save_report = _no_report
    inv = inventory()
    inv.ap_groups.append({"id": "g102", "name": "unit-102"})   # as after Create WLANs
    plan = plan_ap_mapping(snapshot(), inv, CHOICES, add_new_aps=True)
    r1 = FakeR1()

    async def get_aps(t, v):
        return {"data": [{"serialNumber": s, "apGroupId": g} for s, g in r1.where.items()]}
    r1.venues = SimpleNamespace(get_aps_by_tenant_venue=get_aps)
    p = _phase(phases.MapApsPhase, plan, r1)
    try:
        asyncio.run(p.execute(p.Inputs(sz_snapshot_job_id="s")))
        raised = None
    except RuntimeError as e:
        raised = str(e)
    f += check("moves AP-0 and AP-4; adds AP-1 (not in R1) into unit-102",
               sorted((x[0], x[1]) for x in r1.requests) ==
               [("POST", "/venues/v/apGroups/g102/aps"), ("PUT", "/venues/v/apGroups/g101/aps/S0"),
                ("PUT", "/venues/v/apGroups/r1-default/aps/S4")], str(r1.requests))
    f += check("all confirmed, nothing raised", raised is None, str(raised))
    f += check("Map APs is non-critical", next(ph for ph in SZApMappingWorkflow.phases
                                               if ph.id == "map_aps").critical is False)
    return f


def test_staging():
    print("\n8. Staging WLANs no AP broadcasts (create + group, not activated)")
    f = 0
    stage = {"stage_unbroadcast": ["w6", "w6b", "w7", "w8", "w9"]}
    layout = build_layout(snapshot(), inventory(), {**CHOICES, **stage})
    st = layout["staged"]
    by = {g["name"]: g for g in st["groups"]}
    f += check("default pairing: SZ WLAN Group name = AP name; a unit's two WLANs share one group",
               sorted(by) == ["AP-6", "AP-7"] and by["AP-6"]["wlan_ids"] == ["w6", "w6b"]
               and [a["name"] for a in by["AP-6"]["aps"]] == ["AP-6"] and by["AP-6"]["create"], str(st))
    f += check("a WLAN pairing with no AP is reported, still created",
               "Orphan" in st["unpaired"] and next(u for u in layout["unbroadcast"] if u["id"] == "w8")["included"],
               str(st["unpaired"]))
    f += check("an AP with a group from its SZ broadcast is never staged",
               any("AP-0" in c for c in st["conflicts"]) and "Unit100" in st["unpaired"], str(st["conflicts"]))
    f += check("staged APs leave the default group",
               {"AP-6", "AP-7"}.isdisjoint(a["name"] for a in layout["default_group"]["aps"]))
    acts = wlan_activations(layout)
    f += check("staged WLANs are created, never activated", all(acts[w]["mode"] == "none" for w in ("w6", "w6b", "w7")),
               str({w: acts.get(w) for w in ("w6", "w7")}))

    none = build_layout(snapshot(), inventory(), CHOICES)
    cand = {g["name"]: g for g in none["staged"]["groups"]}
    f += check("not staged: candidate per-unit groups still listed (tickable), none chosen or built",
               sorted(cand) == ["AP-6", "AP-7"] and not any(g["chosen"] or g["create"] for g in cand.values())
               and none["summary"]["staged_groups"] == 0 and "AP-6" in [a["name"] for a in none["default_group"]["aps"]],
               str({k: (g["chosen"], g["create"]) for k, g in cand.items()}))
    rows = {u["name"]: u for u in none["unbroadcast"]}
    f += check("every WLAN row knows its AP Group, staged or not; unpaired has none",
               rows["Unit106"]["paired_group"] == "AP-6" and rows["Orphan"]["paired_group"] is None
               and none["staged"]["unpaired"] == [], str(rows["Unit106"]))
    one = build_layout(snapshot(), inventory(), {**CHOICES, "stage_unbroadcast": ["w7"]})
    f += check("ticking one group builds only that one",
               [(g["name"], g["chosen"]) for g in one["staged"]["groups"]] == [("AP-6", False), ("AP-7", True)],
               str([(g["name"], g["chosen"]) for g in one["staged"]["groups"]]))

    pat = build_layout(snapshot(), inventory(), {**CHOICES, "stage_unbroadcast": ["w6", "w7", "w8"],
                                                 "pairing": {"mode": "pattern", "wlan_pattern": "Unit10{unit}",
                                                             "ap_pattern": "AP-{unit}"}})
    f += check("pattern pairing: Unit10{unit} -> AP-{unit}",
               sorted((g["name"], g["aps"][0]["name"]) for g in pat["staged"]["groups"])
               == [("Unit106", "AP-6"), ("Unit107", "AP-7")] and pat["staged"]["unpaired"] == ["Orphan"],
               str(pat["staged"]))

    plan = plan_ap_mapping(snapshot(), inventory(), {**CHOICES, **stage})
    ap6 = next(a for a in plan["aps"] if a["name"] == "AP-6")
    f += check("before Create WLANs builds it, AP-6 moves into its staged group, created by map_aps",
               (ap6["action"], ap6["group_name"], ap6["group_create"]) == ("move", "AP-6", True)
               and "AP-6" in plan["create_groups"], str(ap6))
    inv = inventory()
    inv.ap_groups.append({"id": "g-stage-6", "name": "AP-6"})
    ap6 = next(a for a in plan_ap_mapping(snapshot(), inv, {**CHOICES, **stage})["aps"] if a["name"] == "AP-6")
    f += check("after, AP Mapping moves AP-6 into it", (ap6["action"], ap6["group_id"]) == ("move", "g-stage-6"),
               str(ap6))
    f += check("no activation planned for staged WLANs; staged groups counted",
               not {"Unit106", "Unit107"} & {a["name"] for a in plan["activations"]}
               and plan["counts"]["groups_staged"] == 2, str(plan["counts"]))
    return f


def test_selection():
    print("\n9. AP table selection")
    f = 0
    built = inventory()
    built.ap_groups.append({"id": "g102", "name": "unit-102"})
    by = lambda sel: {a["name"]: a for a in plan_ap_mapping(snapshot(), built, CHOICES, selected=sel)["aps"]}
    a = by(["S0"])
    f += check("only picked APs move; an unpicked move reads not_selected; an unpicked new AP isn't added",
               (a["AP-0"]["action"], a["AP-4"]["action"], a["AP-1"]["action"]) == ("move", "not_selected", "not_in_r1"),
               str({k: a[k]["action"] for k in ("AP-0", "AP-4", "AP-1")}))
    a = by(["S1", "S4"])
    f += check("a picked AP not in R1 is added; picked move moves",
               (a["AP-1"]["action"], a["AP-4"]["action"], a["AP-0"]["action"]) == ("add", "move", "not_selected"),
               str({k: a[k]["action"] for k in ("AP-0", "AP-4", "AP-1")}))
    f += check("rows say what the AP will broadcast and its SZ side",
               a["AP-3"]["broadcast"] == ["Guest", "Secure6"] and "Unit102" in a["AP-1"]["broadcast"]
               and a["AP-3"]["sz_ap_group"] == "default", str(a["AP-1"]))
    # An AP whose AP Group wasn't built: picked anyway, it goes to the default group
    a = by(["S2"])
    f += check("unbuilt group, not picked: stays unmapped", by([])["AP-2"]["action"] == "unmapped")
    f += check("unbuilt group, picked: placed in the default group, flagged, broadcasting the All AP Groups set",
               a["AP-2"]["fallback_default"] and a["AP-2"]["group_id"] == "r1-default"
               and a["AP-2"]["action"] == "already" and a["AP-2"]["wanted_group"] == "unit-103"
               and a["AP-2"]["broadcast"] == ["Guest", "Secure6"], str(a["AP-2"]))
    # Duplicate AP names: R1 needs them unique ("Hallway" twice failed an add)
    snap = snapshot()
    snap.aps[3]["deviceName"] = "Hallway"; snap.aps[6]["deviceName"] = "Hallway"
    dup = {x["serial"]: x for x in plan_ap_mapping(snap, built, CHOICES)["aps"]}
    f += check("duplicate names flagged and planned as <name>_<serial>",
               dup["S3"]["name_clash"] and dup["S3"]["r1_name"] == "Hallway_S3" and dup["S6"]["r1_name"] == "Hallway_S6"
               and not dup["S4"]["name_clash"] and dup["S4"]["r1_name"] == "AP-4", str((dup["S3"], dup["S4"]["r1_name"])))
    typed = {x["serial"]: x for x in plan_ap_mapping(snap, built, CHOICES, renames={"S6": "Hallway East"})["aps"]}
    f += check("a typed name wins; the other keeps the <name>_<serial> default",
               typed["S6"]["r1_name"] == "Hallway East" and typed["S3"]["r1_name"] == "Hallway_S3")
    kept = {x["serial"]: x for x in plan_ap_mapping(snap, built, CHOICES, name_policy="keep")["aps"]}
    f += check("'keep' leaves the names (flagged)", kept["S3"]["r1_name"] == "Hallway" and kept["S3"]["name_clash"])

    # R1 AP names take 32 characters (prod: 13 adds "Invalid attribute value")
    from services.sz_migration.ap_mapping import split_ap_name, cut_ap_name, r1_ap_name_problem
    f += check("split at the delimiter: 'Building A - in Regency room behind Concierge desk'",
               split_ap_name("Building A - in Regency room behind Concierge desk")
               == ("Building A", "in Regency room behind Concierge desk"))
    head, rest = cut_ap_name("Building A - near entrance to auxiliary gym & ladies room")
    f += check("cut on a word when no split fits", head == "Building A - near entrance to" and rest == "auxiliary gym & ladies room"
               and not r1_ap_name_problem(head), str((head, rest)))
    head, _ = cut_ap_name("X" * 45)
    f += check("no space at all: hard cut at 32", len(head) == 32, str(len(head)))
    f += check("name rules: >32, edge space, backtick refused; 32 plain accepted",
               r1_ap_name_problem("x" * 33) and r1_ap_name_problem(" ab") and r1_ap_name_problem("a`b")
               and not r1_ap_name_problem("x" * 32))
    snap = snapshot()
    snap.aps[3]["deviceName"] = "Building A - in Regency room behind Concierge desk"
    snap.aps[4]["deviceName"] = "Building B - near Mayfair card room"
    snap.aps[6]["deviceName"] = "Long name without any delimiter at all here"
    snap.aps[4]["description"] = "N/A"
    snap.aps[6]["description"] = "2nd floor"
    lng = {x["serial"]: x for x in plan_ap_mapping(snap, built, CHOICES)["aps"]}
    f += check("unique split: name before the delimiter, the rest as description",
               (lng["S3"]["r1_name"], lng["S3"]["r1_description"], lng["S3"]["name_fix"])
               == ("Building A", "in Regency room behind Concierge desk", "split")
               and lng["S4"]["r1_description"] == "near Mayfair card room", str((lng["S3"], lng["S4"]["r1_description"])))
    f += check("no delimiter: cut on a word, flagged 'cut', SZ's own description kept after the rest",
               lng["S6"]["name_fix"] == "cut" and len(lng["S6"]["r1_name"]) <= 32
               and lng["S6"]["r1_description"].endswith("| 2nd floor"), str(lng["S6"]))
    snap.aps[4]["deviceName"] = "Building A - near entrance to auxiliary gym & ladies room"
    same = {x["serial"]: x for x in plan_ap_mapping(snap, built, CHOICES)["aps"]}
    f += check("two splits would both be 'Building A': each cut on a word instead, names differ",
               same["S3"]["name_fix"] == same["S4"]["name_fix"] == "cut"
               and same["S3"]["r1_name"] != same["S4"]["r1_name"]
               and not same["S3"]["name_clash"], str((same["S3"]["r1_name"], same["S4"]["r1_name"])))
    typed = {x["serial"]: x for x in plan_ap_mapping(snap, built, CHOICES, renames={"S1": "x" * 40},
                                                      descriptions={"S3": "Regency room"})["aps"]}
    f += check("a typed name over 32 on an add -> name_error; a typed description wins",
               typed["S1"]["name_error"] and typed["S1"]["name_fix"] == "typed"
               and typed["S3"]["r1_description"] == "Regency room", str((typed["S1"], typed["S3"]["r1_description"])))
    staged = {x["name"]: x for x in plan_ap_mapping(snapshot(), inventory(), {**CHOICES, "stage_unbroadcast": ["w7"]})["aps"]}
    f += check("a staged AP broadcasts only the All AP Groups set (its group carries nothing active)",
               staged["AP-7"]["staged"] and staged["AP-7"]["broadcast"] == ["Guest", "Secure6"], str(staged["AP-7"]))
    return f


def test_graph():
    print("\n7. Graph")
    f = 0
    ph = {p.id: p for p in SZApMappingWorkflow.phases}
    order = ["ap_mapping_plan", "map_aps", "verify_ap_mapping"]
    f += check("APs only: plan -> map -> verify (no group creation, no activation)",
               all(ph[b].depends_on == [a] for a, b in zip(order, order[1:])), str({k: v.depends_on for k, v in ph.items()}))
    f += check("critical: plan, verify; not map", [ph[i].critical for i in order] == [True, False, True])
    f += check("exactly those phases", sorted(ph) == sorted(order), str(sorted(ph)))
    return f




def test_place_by_name():
    print("\n10. Place by name (SZ had the APs in the wrong AP Groups)")
    f = 0
    site = "Site"
    w = {k: SZWLANFull(id=k, name=n, ssid=n, auth_type="WPA2-PSK", encryption={"method": "WPA2"})
         for k, n in (("wifi", "WiFi"), ("u124", f"4-124@{site}"), ("u125", f"4-125@{site}"), ("u126", f"4-126@{site}"))}
    m = {k: SZWLANGroupMember(id=v.id, name=v.name, ssid=v.ssid) for k, v in w.items()}
    wgs = [SZWLANGroup(id="wg-d", name="default", members=[m["wifi"]])] + [
        SZWLANGroup(id=f"wg-{u}", name=u, members=[m["wifi"], m[f"u{u}"]]) for u in ("124", "125", "126")]
    # Swapped in SZ: AP 4-124 broadcasts 4-125's WLAN and vice versa; 4-126 is right; Lobby has no unit.
    plan_of = {"4-124": "wg-125", "4-125": "wg-124", "4-126": "wg-126", "Lobby": None}
    aps = [{"apMac": n, "deviceName": n, "serial": f"S{n}", "apGroupId": "sz-default"} for n in plan_of]
    detail = {n: {"dual5g": False, "radios": {b: {"wlan_group_id": wg, "service_enabled": True}
                                              for b in ("radio24g", "radio5g")}} for n, wg in plan_of.items()}
    snap = SZMigrationSnapshot(zone=SZZoneSnapshot(id="z", name="Z"), wlans=list(w.values()), wlan_groups=wgs,
                               aps=aps, ap_radio_config=detail,
                               ap_groups=[SZAPGroupEnriched(id="sz-default", name="default", ap_count=len(aps))])

    def net(nid, ssid, groups=None):
        vag = {"venueId": "v", "isAllApGroups": groups is None, "apGroupIds": groups or []}
        return {"id": nid, "name": ssid, "ssid": ssid, "venueApGroups": [vag]}
    # R1 groups built by name; 4-126's group is called "Unit 126" but carries its unit SSID.
    inv = R1VenueInventory(
        venue_id="v", venue_name="V", tenant_id="t",
        ap_groups=[{"id": "g-d", "name": "", "isDefault": True}, {"id": "g124", "name": "4-124"},
                   {"id": "g125", "name": "4-125"}, {"id": "g126", "name": "Unit 126"}],
        wifi_networks=[net("n0", "WiFi"), net("n124", f"4-124@{site}", ["g124"]),
                       net("n125", f"4-125@{site}", ["g125"]), net("n126", f"4-126@{site}", ["g126"])],
        aps=[{"serialNumber": f"S{n}", "name": n, "apGroupId": "g-d"} for n in plan_of])
    row = lambda p, n: next(a for a in p["aps"] if a["name"] == n)

    p = plan_ap_mapping(snap, inv)
    f += check("off: SZ's (wrong) broadcast decides -- 4-124 goes to 4-125's group",
               row(p, "4-124")["group_name"] == "4-125" and not row(p, "4-124")["by_name"], str(row(p, "4-124")))
    f += check("off: the two swapped APs are counted, and say where their name points",
               p["counts"]["aps_name_differs"] == 2 and row(p, "4-124")["name_group"] == "4-124"
               and p["counts"]["aps_by_name"] == 0, str(p["counts"]))

    p = plan_ap_mapping(snap, inv, place_by_name=True)
    a = row(p, "4-124")
    f += check("on: 4-124 goes to the group named 4-124, SZ's target kept for display",
               a["group_id"] == "g124" and a["by_name"] and a["sz_group_name"] == "4-125" and a["action"] == "move",
               str(a))
    f += check("on: it will broadcast its name group's unit SSID",
               f"4-124@{site}" in a["broadcast"] and f"4-125@{site}" not in a["broadcast"], str(a["broadcast"]))
    b = row(p, "4-126")
    f += check("an AP already right is left alone (not by_name)", b["group_id"] == "g126" and not b["by_name"], str(b))
    f += check("no name match: unchanged", row(p, "Lobby")["group_key"] == "__default__" and not row(p, "Lobby")["by_name"])
    f += check("counts", p["counts"]["aps_by_name"] == 2, str(p["counts"]))

    # Strays: APs with no unit group of their own, broadcasting 4-124's unit WLAN (Novo: 223 APs
    # fell back to WLAN Group "3109"); "4-124-B" is a second AP in unit 4-124, not a stray.
    stray = {"5101": "wg-124", "Club": "wg-124", "4-124-B": "wg-124"}
    snap2 = snap.model_copy(update={
        "aps": snap.aps + [{"apMac": n, "deviceName": n, "serial": f"S{n}", "apGroupId": "sz-default"} for n in stray],
        "ap_radio_config": {**detail, **{n: {"dual5g": False, "radios": {b: {"wlan_group_id": wg, "service_enabled": True}
                                                                         for b in ("radio24g", "radio5g")}}
                                         for n, wg in stray.items()}}})
    p = plan_ap_mapping(snap2, inv)
    f += check("off: strays follow SZ into 4-124's group; name says its own unit '5101', or default (Club)",
               row(p, "5101")["group_id"] == "g124" and row(p, "5101")["name_group"] == "5101"
               and row(p, "Club")["name_group"] == "(default)", str(row(p, "5101")))
    f += check("a second AP in the unit isn't a stray", row(p, "4-124-B")["name_group"] is None, str(row(p, "4-124-B")))
    p = plan_ap_mapping(snap2, inv, place_by_name=True)
    f += check("on: a stray with no unit in its name goes to the default group (All AP Groups set only)",
               row(p, "Club")["group_key"] == "__default__" and row(p, "Club")["by_name"]
               and row(p, "Club")["broadcast"] == ["WiFi"], str(row(p, "Club")))
    a = row(p, "5101")
    f += check("on: a stray named after a unit with no group gets that group, created",
               (a["group_name"], a["by_name"], a["group_create"], a["broadcast"]) == ("5101", True, True, ["WiFi"]),
               str(a))
    f += check("on: the unit's own APs stay", row(p, "4-124-B")["group_id"] == "g124" and not row(p, "4-124-B")["by_name"])

    # Wider match: "2-2045" finds group "2045" (the unit number alone).
    inv2 = inv.model_copy(update={"ap_groups": inv.ap_groups + [{"id": "g2045", "name": "2045"}]})
    snap3 = snap2.model_copy(update={
        "aps": snap2.aps + [{"apMac": "2-2045", "deviceName": "2-2045", "serial": "S2-2045", "apGroupId": "sz-default"}],
        "ap_radio_config": {**snap2.ap_radio_config, "2-2045": snap2.ap_radio_config["5101"]}})
    p = plan_ap_mapping(snap3, inv2, place_by_name=True)
    f += check("'2-2045' -> group '2045'", row(p, "2-2045")["group_id"] == "g2045" and row(p, "2-2045")["by_name"],
               str(row(p, "2-2045")))

    # Manual overrides beat both; a name R1 lacks is "missing" and its AP waits.
    ov = {"SClub": "4-125", "S5101": "Pool House", "SLobby": "(default)", "S4-126": "  "}
    p = plan_ap_mapping(snap3, inv2, place_by_name=True, overrides=ov)
    a = row(p, "Club")
    f += check("manual -> an R1 group (any case match), status exists, SZ's target kept for display",
               a["group_id"] == "g125" and a["manual"] and a["manual_status"] == "exists"
               and a["sz_group_name"] == "4-124" and a["auto_group"] == "(default)", str(a))
    a = row(p, "5101")
    f += check("manual -> a group R1 lacks: missing, listed, to be created (only once the AP is added)",
               a["manual_status"] == "missing" and a["action"] == "not_in_r1" and a["group_create"]
               and p["manual_missing"] == ["Pool House"] and "Pool House" not in p["create_groups"],
               str((a, p["manual_missing"])))
    p = plan_ap_mapping(snap3, inv2, place_by_name=True, overrides=ov, add_new_aps=True)
    f += check("... added: 'Pool House' is in create_groups", row(p, "5101")["action"] == "add"
               and p["create_groups"] == ["Pool House"], str(p["create_groups"]))
    f += check("manual '(default)' -> the default group", row(p, "Lobby")["group_key"] == "__default__"
               and row(p, "Lobby")["manual"])
    f += check("a blank override is ignored", not row(p, "4-126")["manual"])
    f += check("count", p["counts"]["aps_manual"] == 3, str(p["counts"]))

    # Unit match: an AP named after a unit SSID, the group named something else.
    detail["4-126"]["radios"] = {b: {"wlan_group_id": "wg-124", "service_enabled": True} for b in ("radio24g", "radio5g")}
    snap = snap.model_copy(update={"ap_radio_config": detail})
    p = plan_ap_mapping(snap, inv, place_by_name=True)
    c = row(p, "4-126")
    f += check("unit match: 4-126 -> 'Unit 126' (carries 4-126@Site)",
               c["group_id"] == "g126" and c["by_name"], str(c))
    return f


def test_by_name_tokens():
    print("\n11. By name: unit as a token anywhere in the name; groups Create WLANs didn't build")
    f = 0
    site = "Site"
    units = ("20102", "2004", "3003")
    w = {"wifi": SZWLANFull(id="wifi", name="WiFi", ssid="WiFi", auth_type="WPA2-PSK", encryption={"method": "WPA2"})}
    w.update({u: SZWLANFull(id=f"w{u}", name=f"{u}@{site}", ssid=f"{u}@{site}", auth_type="WPA2-PSK",
                            encryption={"method": "WPA2"}) for u in units})
    m = {k: SZWLANGroupMember(id=v.id, name=v.name, ssid=v.ssid) for k, v in w.items()}
    wgs = [SZWLANGroup(id="wg-d", name="default", members=[m["wifi"]])] + [
        SZWLANGroup(id=f"wg-{u}", name=u, members=[m["wifi"], m[u]]) for u in units]
    # "*20102-NEW" and "*2004" broadcast their own unit; "2004" (a second AP in 2004) and
    # "*3003-old" broadcast only WiFi in SZ (lost their override); "Lobby" has no unit.
    plan_of = {"*20102-NEW": "wg-20102", "*2004": "wg-2004", "2004": None, "*3003-old": None, "Lobby": None,
               "3003": "wg-3003"}
    aps = [{"apMac": n, "deviceName": n, "serial": f"S{i}", "apGroupId": "sz-default"} for i, n in enumerate(plan_of)]
    detail = {n: {"dual5g": False, "radios": {b: {"wlan_group_id": wg, "service_enabled": True}
                                              for b in ("radio24g", "radio5g")}} for n, wg in plan_of.items()}
    snap = SZMigrationSnapshot(zone=SZZoneSnapshot(id="z", name="Z"), wlans=list(w.values()), wlan_groups=wgs,
                               aps=aps, ap_radio_config=detail,
                               ap_groups=[SZAPGroupEnriched(id="sz-default", name="default", ap_count=len(aps))])
    # R1: only 3003's group was built (named "Unit 3003"); 20102 and 2004 were not ticked in Create WLANs.
    inv = R1VenueInventory(
        venue_id="v", venue_name="V", tenant_id="t",
        ap_groups=[{"id": "g-d", "name": "", "isDefault": True}, {"id": "g3003", "name": "Unit 3003"}],
        wifi_networks=[{"id": "n0", "name": "WiFi", "ssid": "WiFi",
                        "venueApGroups": [{"venueId": "v", "isAllApGroups": True}]},
                       {"id": "n3003", "name": f"3003@{site}", "ssid": f"3003@{site}",
                        "venueApGroups": [{"venueId": "v", "isAllApGroups": False, "apGroupIds": ["g3003"]}]}],
        aps=[{"serialNumber": f"S{i}", "name": n, "apGroupId": "g-d"} for i, n in enumerate(plan_of)])
    row = lambda p, n: next(a for a in p["aps"] if a["name"] == n)

    p = plan_ap_mapping(snap, inv)
    f += check("off: '*20102-NEW' has no AP Group built -> unmapped, as before",
               row(p, "*20102-NEW")["action"] == "unmapped", str(row(p, "*20102-NEW")))
    f += check("off: '2004' and '*3003-old' are flagged with the group their name points at",
               row(p, "2004")["name_group"] == "2004" and row(p, "*3003-old")["name_group"] == "Unit 3003",
               str((row(p, "2004"), row(p, "*3003-old"))))

    p = plan_ap_mapping(snap, inv, place_by_name=True)
    for n, g in (("*20102-NEW", "20102"), ("*2004", "2004"), ("2004", "2004")):
        a = row(p, n)
        f += check(f"on: '{n}' -> group '{g}', created by map_aps, moved there",
                   (a["group_name"], a["by_name"], a["group_create"], a["action"]) == (g, True, True, "move"), str(a))
    a = row(p, "*3003-old")
    f += check("on: '*3003-old' -> existing 'Unit 3003' (via 3003@Site)",
               (a["group_id"], a["by_name"], a["action"]) == ("g3003", True, "move"), str(a))
    f += check("on: no unit in the name -> unchanged", row(p, "Lobby")["group_key"] == "__default__"
               and not row(p, "Lobby")["by_name"])
    f += check("on: a group not built broadcasts only the All AP Groups set (AP Mapping doesn't activate)",
               row(p, "*2004")["broadcast"] == ["WiFi"], str(row(p, "*2004")["broadcast"]))
    f += check("create_groups lists each once", p["create_groups"] == ["2004", "20102"], str(p["create_groups"]))

    # Echo Lake SG: no unit WLANs at all, R1 has other units' groups -- the group comes from the AP's name.
    names = ["*2001", "2001", "19101", "*20102-NEW", "Maint Shop", "101-102", "3003"]
    snap2 = SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="SG"), wlans=[w["wifi"]], wlan_groups=[wgs[0]],
        aps=[{"apMac": n, "deviceName": n, "serial": f"T{i}", "apGroupId": "sz-default"} for i, n in enumerate(names)],
        ap_radio_config={n: {"dual5g": False, "radios": {b: {"wlan_group_id": None, "service_enabled": True}
                                                         for b in ("radio24g", "radio5g")}} for n in names},
        ap_groups=[SZAPGroupEnriched(id="sz-default", name="default", ap_count=len(names))])
    p = plan_ap_mapping(snap2, inv, place_by_name=True, add_new_aps=True)
    got = {n: (row(p, n)["group_name"], row(p, n)["group_create"]) for n in names}
    f += check("no group anywhere: named after the unit in the AP name, created; '*2001' and '2001' share one",
               got == {"*2001": ("2001", True), "2001": ("2001", True), "19101": ("19101", True),
                       "*20102-NEW": ("20102", True), "Maint Shop": ("(default)", False),
                       "101-102": ("(default)", False), "3003": ("Unit 3003", False)}
               and p["create_groups"] == ["19101", "2001", "20102"], str((got, p["create_groups"])))
    return f


class GroupR1(FakeR1):
    """FakeR1 plus AP Group find/create; `fail` names refuse to create."""

    def __init__(self, existing=None, fail=()):
        super().__init__()
        self.groups = dict(existing or {})      # name -> id
        self.created, self.fail = [], set(fail)
        self.venues = SimpleNamespace(find_ap_group_by_name=self._find, create_ap_group=self._create,
                                      get_aps_by_tenant_venue=self._aps)

    async def _find(self, t, v, name):
        gid = next((i for n, i in self.groups.items() if n.casefold() == name.casefold()), None)
        return {"id": gid, "name": name} if gid else None

    async def _create(self, t, v, name, wait_for_completion=True):
        if name in self.fail:
            raise RuntimeError("R1 said no")
        self.groups[name] = f"new-{name}"
        self.created.append(name)
        return {"id": f"new-{name}", "name": name}

    async def _aps(self, t, v):
        return {"data": [{"serialNumber": s, "apGroupId": g} for s, g in self.where.items()]}


def test_map_creates_groups():
    print("\n12. map_aps creates the AP Groups its APs go into, then places them")
    f = 0
    phases.save_report = _no_report
    # AP-1 goes into ticked unit-102 (not built); AP-3 manually into "Pool House", AP-8 into "Broken"
    plan = plan_ap_mapping(snapshot(), inventory(), CHOICES, add_new_aps=True,
                           overrides={"S3": "Pool House", "S8": "Broken"})
    r1 = GroupR1(fail={"Broken"})
    p = _phase(phases.MapApsPhase, plan, r1)
    changes = []

    async def rec(action, rtype, name, resource_id=None, detail=""):
        changes.append((action, rtype, name))
    p.record_change = rec
    try:
        asyncio.run(p.execute(p.Inputs(sz_snapshot_job_id="s")))
        raised = None
    except RuntimeError as e:
        raised = str(e)
    f += check("creates the groups R1 lacks", sorted(r1.created) == ["Pool House", "unit-102"], str(r1.created))
    reqs = sorted((x[0], x[1]) for x in r1.requests)
    f += check("then places APs into the new groups",
               ("POST", "/venues/v/apGroups/new-unit-102/aps") in reqs
               and ("PUT", "/venues/v/apGroups/new-Pool House/aps/S3") in reqs, str(reqs))
    f += check("an AP whose group failed to create is not sent, and fails the phase",
               not any("S8" in x[1] for x in reqs) and raised and "AP-8" in raised
               and ("failed", "ap_groups", "Broken") in changes, str((raised, changes)))
    f += check("created groups logged", ("created", "ap_groups", "Pool House") in changes, str(changes))
    return f


def test_activate_unbroadcast():
    print("\n13. Activating WLANs no AP broadcasts on their unit's AP Group")
    f = 0
    snap = snapshot()
    # "unit-102@Site": no WLAN Group, unit from the name -> the derived unit-102 group.
    # "Lobby": no unit at all -> All AP Groups.
    snap.wlans += [SZWLANFull(id="w10", name="unit-102@Site", ssid="u102-iot", auth_type="WPA2-PSK",
                              encryption={"method": "WPA2"}),
                   SZWLANFull(id="w11", name="Lobby", ssid="Lobby", auth_type="WPA2-PSK",
                              encryption={"method": "WPA2"})]
    ids = ["w6", "w6b", "w7", "w8", "w9", "w10", "w11"]
    choices = {**CHOICES, "activate_unbroadcast": ids}
    layout = build_layout(snap, inventory(), choices)
    rows = {u["id"]: u for u in layout["unbroadcast"]}
    by = {g["name"]: g for g in layout["staged"]["groups"]}
    acts = wlan_activations(layout)
    tgt = lambda w: [(t["r1_ap_group_name"], t["r1_ap_group_create"]) for t in acts[w].get("targets", [])]

    f += check("paired: Unit106 + Unit106-IoT activated on AP-6, built and filled with AP-6",
               by["AP-6"]["chosen"] and by["AP-6"]["active_ssids"] == ["Unit106", "Unit106-IoT"]
               and [a["name"] for a in by["AP-6"]["aps"]] == ["AP-6"]
               and tgt("w6") == [("AP-6", True)] and tgt("w6b") == [("AP-6", True)], str((by.get("AP-6"), acts["w6"])))
    f += check("unpaired, own WLAN Group: Orphan gets an empty 'nobody' group, activated",
               by["nobody"]["ap_count"] == 0 and by["nobody"]["create"] and tgt("w8") == [("nobody", True)],
               str(by.get("nobody")))
    f += check("its unit's AP already has a broadcast group: Unit100 still gets group 'AP-0', empty",
               by["AP-0"]["ap_count"] == 0 and tgt("w9") == [("AP-0", True)] and not layout["staged"]["conflicts"],
               str((by.get("AP-0"), layout["staged"]["conflicts"])))
    g102 = next(g for g in layout["groups"] if g["key"] == K102)
    f += check("unit from 'unit-102@Site' joins the SZ-derived unit-102 group",
               "w10" in g102["wlan_ids"] and "u102-iot" in g102["ssids"] and rows["w10"]["unit_group"] == K102
               and tgt("w10") == [("unit-102", True)], str((g102["ssids"], acts["w10"])))
    f += check("no unit: Lobby goes on All AP Groups, counted toward every group's 15",
               acts["w11"]["mode"] == "all_ap_groups" and rows["w11"]["activation"] == "all_ap_groups"
               and g102["ssid_total"] == len({"Guest", "Secure6", "Lobby", "Unit102", "u102-iot"}),
               str((acts["w11"], g102["ssid_total"])))
    f += check("radios for an un-broadcast WLAN: the zone's bands", acts["w8"]["targets"][0]["r1_radio_types"]
               == ["2.4-GHz", "5-GHz"], str(acts["w8"]))
    s = layout["summary"]
    f += check("summary counts activated + empty unit groups",
               s["activated_unbroadcast"] == 7 and s["activated_all_ap"] == 1 and s["unit_groups_empty"] == 2, str(s))
    f += check("no clash, not blocked", not layout["blocked"], str(layout["warnings"]))

    plan = plan_ap_mapping(snap, inventory(), choices)
    ap = {a["name"]: a for a in plan["aps"]}
    f += check("AP Mapping: AP-6 broadcasts its activated unit WLANs; every AP gets Lobby",
               {"Unit106", "Unit106-IoT", "Lobby"} <= set(ap["AP-6"]["broadcast"])
               and "Lobby" in ap["AP-3"]["broadcast"], str((ap["AP-6"]["broadcast"], ap["AP-3"]["broadcast"])))
    f += check("AP Mapping: AP-1 (unit-102) broadcasts the joined u102-iot",
               "u102-iot" in ap["AP-1"]["broadcast"], str(ap["AP-1"]["broadcast"]))
    f += check("verify knows what was activated on purpose",
               set(plan["activated_unbroadcast"]) == {"Unit106", "Unit106-IoT", "Unit107", "Orphan", "Unit100",
                                                      "u102-iot", "Lobby"}, str(plan["activated_unbroadcast"]))

    # An earlier run's empty unit group in R1: reused, not created again
    inv = inventory()
    inv.ap_groups.append({"id": "g-nobody", "name": "nobody"})
    again = build_layout(snap, inv, choices)
    nb = next(g for g in again["staged"]["groups"] if g["name"] == "nobody")
    f += check("re-run: the R1 group of that name is reused", nb["exists"] and not nb["create"]
               and wlan_activations(again)["w8"]["targets"][0]["r1_ap_group_id"] == "g-nobody", str(nb))

    # Mixed: one WLAN of a paired group staged, the other activated
    mixed = build_layout(snap, inventory(), {**CHOICES, "stage_unbroadcast": ["w6b"], "activate_unbroadcast": ["w6"]})
    g6 = next(g for g in mixed["staged"]["groups"] if g["name"] == "AP-6")
    macts = wlan_activations(mixed)
    f += check("mixed group: only the activated WLAN is active there",
               g6["active_ssids"] == ["Unit106"] and macts["w6b"]["mode"] == "none"
               and [t["r1_ap_group_name"] for t in macts["w6"]["targets"]] == ["AP-6"], str((g6, macts.get("w6b"))))
    return f


async def _verify(plan, broadcast_aps):
    """Run verify_ap_mapping against a stubbed plan + audit."""
    p = phases.VerifyApMappingPhase.__new__(phases.VerifyApMappingPhase)
    saved = {}
    async def fresh(_):
        return None, None, plan
    async def emit(*a, **k):
        pass
    p.fresh, p.emit, p.job_id = fresh, emit, "j"
    orig_audit, orig_save, orig_match = phases.build_broadcast_audit, phases.save_report, phases.build_matching
    phases.build_broadcast_audit = lambda *a: SimpleNamespace(aps=broadcast_aps, counts={"aps_compared": len(broadcast_aps)})
    phases.build_matching = lambda *a: None
    async def save(job, kind, report):
        saved[kind] = report
    phases.save_report = save
    try:
        try:
            await p.execute(p.Inputs(sz_snapshot_job_id="s"))
            err = None
        except RuntimeError as e:
            err = str(e)
    finally:
        phases.build_broadcast_audit, phases.save_report, phases.build_matching = orig_audit, orig_save, orig_match
    return err, saved.get("verify")


def test_verify_on_purpose():
    print("\n14. Verify: gaining a WLAN activated on purpose is not a difference")
    f = 0
    counts = {k: 0 for k in ("aps", "aps_unmapped", "aps_blocked", "aps_not_in_r1", "aps_keep", "aps_already",
                             "wlans", "wlans_no_network", "wlans_already")}
    plan = {"counts": counts, "aps": [], "activations": [], "create_groups": [], "groups": [],
            "activated_unbroadcast": ["Lobby"]}
    ap = lambda n, extra, missing=(): SimpleNamespace(name=n, serial=n, status="differs", extra_in_r1=list(extra),
                                                       missing_in_r1=list(missing), sz_ap_group="d", r1_ap_group="d")
    err, rep = asyncio.run(_verify(plan, [ap("A", ["Lobby"]), ap("B", ["Lobby", "Other"]), ap("C", ["Lobby"], ["X"])]))
    f += check("only-Lobby AP passes; extra Other or missing X still differ",
               rep["differing_total"] == 2 and rep["gained_on_purpose"] == 1 and err and "2 AP(s)" in err,
               str((err, rep and rep["differing_total"])))
    err, rep = asyncio.run(_verify(plan, [ap("A", ["Lobby"])]))
    f += check("all differences on purpose: the phase passes", err is None and rep["differing_total"] == 0, str(err))
    return f


if __name__ == "__main__":
    failures = sum(t() for t in (test_layout_radios, test_plan, test_place_aps, test_map_phase,
                                 test_graph, test_staging, test_selection, test_name_clash_retry,
                                 test_place_by_name, test_pending_tasks, test_by_name_tokens,
                                 test_map_creates_groups, test_activate_unbroadcast, test_verify_on_purpose))
    print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
    sys.exit(1 if failures else 0)
