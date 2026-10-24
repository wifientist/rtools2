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
  7. Graph: APs only -- plan -> map -> verify; a group the plan needs but R1
     lacks is "group_missing" (re-run Create WLANs), never created here.

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
    f += check("AP-1's group (ticked) isn't in R1 yet: waiting on Create WLANs, not created here",
               (by["AP-1"]["action"], by["AP-1"]["group_name"]) == ("group_missing", "unit-102"), str(by["AP-1"]))
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
    f += check("counts", (c["aps_move"], c["aps_group_missing"], c["aps_already"], c["aps_keep"], c["aps_unmapped"],
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
    rows = [{"serial": "S1", "ap_name": "AP-1", "to_group_id": "gA", "to_group_name": "A", "op": "add"},
            {"serial": "S2", "ap_name": "AP-2", "to_group_id": "gB", "to_group_name": "B", "op": "move"},
            {"serial": "S3", "ap_name": "AP-3", "to_group_id": "gB", "to_group_name": "B", "op": "move"},
            {"serial": "S4", "ap_name": "AP-4", "to_group_id": "gB", "to_group_name": "B", "op": "move"}]

    async def read_aps():
        return [{"serialNumber": s, "apGroupId": g} for s, g in r1.where.items()]

    asyncio.run(ap_group_ops.place_aps(r1, "t", "v", rows, read_aps))
    st = {r["serial"]: r["status"] for r in rows}
    f += check("add -> added, move -> moved, refused -> failed, not visible -> unconfirmed",
               st == {"S1": "added", "S2": "moved", "S3": "failed", "S4": "unconfirmed"}, str(st))
    f += check("add is POST into the group with name + serial",
               ("POST", "/venues/v/apGroups/gA/aps", {"name": "AP-1", "serialNumber": "S1"}) in r1.requests,
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
    f += check("before Create WLANs builds it, AP-6 waits on its staged group",
               (ap6["action"], ap6["group_name"]) == ("group_missing", "AP-6"), str(ap6))
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


if __name__ == "__main__":
    failures = sum(t() for t in (test_layout_radios, test_plan, test_place_aps, test_map_phase,
                                 test_graph, test_staging, test_selection, test_name_clash_retry))
    print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
    sys.exit(1 if failures else 0)
