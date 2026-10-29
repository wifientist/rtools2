#!/usr/bin/env python3
"""
SZ -> R1 migration: AP Group names the operator can see, pick and fix
before anything is created.

SQ@Greenland: APs "4-124", WLANs "4-124@Sq_greenland", but SZ named each
unit's WLAN Group by the last three digits ("124"). The layout names a
group after that WLAN Group, so R1 would get "124" -- and "124" exists in
two buildings. The name was editable but looked like plain text, there
was no other name to pick, and a group already matched by name in R1
could not be renamed at all.

WHAT THIS GUARDS
  1. Each group offers its other reasonable names: the AP's name (one-AP
     groups) and the unit part of each "unit@property" WLAN, suggestion
     first; disagreeing options mark it ambiguous.
  2. A name two sources agree on is one option carrying both sources, so
     "name from unit WLAN" still finds it.
  3. "2-116" is offered before "Clone of 2-116".
  4. A rename (group_names) wins, and clears ambiguous.
  5. A group matched to an R1 group BY NAME keeps its options (it can be
     renamed away from a stale group); one matched by SSIDs has none.
  6. Two groups to create under one name: an error that blocks the plan.
  7. Per-unit (staged) groups get options too.

Usage:
    docker compose exec backend python scripts/test_layout_group_names.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import (
    SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZWLANGroup, SZWLANGroupMember, SZAPGroupEnriched,
)
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.r1_layout import build_layout

SITE = "Sq_greenland"
DEFAULT_APG = "apg-default"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def snapshot():
    """Common WiFi from the zone; three unit APs override to WLAN Groups SZ
    named by the 3-digit unit; AP '999' broadcasts only WiFi, and WLAN
    Group '999' (unused) holds 9-999@Site -- a staging candidate."""
    w = {
        "wifi": SZWLANFull(id="wifi", name="SQ WiFi", ssid="SQ WiFi", auth_type="WPA2-PSK"),
        "u124": SZWLANFull(id="u124", name=f"4-124@{SITE}", ssid=f"4-124@{SITE}", auth_type="WPA2-PSK"),
        "u311": SZWLANFull(id="u311", name=f"2-311@{SITE}", ssid=f"2-311@{SITE}", auth_type="WPA2-PSK"),
        "u116": SZWLANFull(id="u116", name=f"2-116@{SITE}", ssid=f"2-116@{SITE}", auth_type="WPA2-PSK"),
        "c116": SZWLANFull(id="c116", name=f"Clone of 2-116@{SITE}", ssid=f"Clone 2-116@{SITE}", auth_type="WPA2-PSK"),
        "u999": SZWLANFull(id="u999", name=f"9-999@{SITE}", ssid=f"9-999@{SITE}", auth_type="WPA2-PSK"),
    }
    m = {k: SZWLANGroupMember(id=v.id, name=v.name, ssid=v.ssid) for k, v in w.items()}
    groups = [
        SZWLANGroup(id="wg-default", name="default", members=[m["wifi"]]),
        SZWLANGroup(id="wg-124", name="124", members=[m["wifi"], m["u124"]]),
        SZWLANGroup(id="wg-311", name="311", members=[m["wifi"], m["u311"]]),
        SZWLANGroup(id="wg-116", name="116", members=[m["wifi"], m["u116"], m["c116"]]),
        SZWLANGroup(id="wg-999", name="999", members=[m["u999"]]),
    ]
    names = {"a124": ("4-124", "wg-124"), "a311": ("311-new", "wg-311"), "a116": ("2-116", "wg-116"),
             "a999": ("999", None), "a1": ("Lobby", None)}
    aps = [{"apMac": k, "deviceName": n, "serial": k.upper(), "apGroupId": DEFAULT_APG} for k, (n, _) in names.items()]
    detail = {k: {"dual5g": False, "radios": {b: {"wlan_group_id": wg, "service_enabled": True}
                                              for b in ("radio24g", "radio5g")}}
              for k, (_, wg) in names.items()}
    return SZMigrationSnapshot(zone=SZZoneSnapshot(id="z", name="SQ@Greenland"), wlans=list(w.values()),
                               wlan_groups=groups, aps=aps, ap_radio_config=detail,
                               ap_groups=[SZAPGroupEnriched(id=DEFAULT_APG, name="default", ap_count=len(aps))])


def inventory(groups=(), networks=()):
    return R1VenueInventory(venue_id="v", venue_name="V", tenant_id="t",
                            ap_groups=[{"id": "r1-default", "name": "", "isDefault": True}, *groups],
                            wifi_networks=list(networks))


def by_ap(layout, ap_name):
    return next(g for g in layout["groups"] if any(a["name"] == ap_name for a in g["aps"]))


def opts(g):
    return [(o["name"], o["source"]) for o in g["name_options"]]


def main():
    f = 0
    snap = snapshot()

    print("\n1-3. Name options")
    lay = build_layout(snap, inventory())
    g124 = by_ap(lay, "4-124")
    f += check("SZ's '124' is still the suggestion", g124["name"] == "124", g124["name"])
    f += check("AP name and unit WLAN agree on '4-124': one option with both sources",
               opts(g124) == [("124", "SZ AP-level WLAN Group"), ("4-124", "AP name / unit WLAN")], str(opts(g124)))
    f += check("ambiguous", g124["ambiguous"] is True)
    g311 = by_ap(lay, "311-new")
    f += check("'311' / AP '311-new' / unit '2-311': three options",
               [n for n, _ in opts(g311)] == ["311", "311-new", "2-311"], str(opts(g311)))
    g116 = by_ap(lay, "2-116")
    units = [n for n, s in opts(g116) if "unit WLAN" in s]
    f += check("'2-116' before 'Clone of 2-116'", units[:2] == ["2-116", "Clone of 2-116"], str(opts(g116)))

    print("\n4. Rename")
    lay = build_layout(snap, inventory(), {"group_names": {g124["key"]: "4-124"}})
    g = by_ap(lay, "4-124")
    f += check("rename wins, marked renamed, no longer ambiguous",
               g["name"] == "4-124" and g["renamed"] and not g["ambiguous"], str((g["name"], g["ambiguous"])))
    f += check("options still offered after a rename", len(g["name_options"]) == 2)

    print("\n5. Existing R1 groups")
    lay = build_layout(snap, inventory([{"id": "r1-124", "name": "124"}]))
    g = by_ap(lay, "4-124")
    f += check("matched by name: exists, options kept so it can be renamed",
               g["exists"] and g["matched_by"] == "name" and len(g["name_options"]) == 2, str(g["matched_by"]))
    lay = build_layout(snap, inventory([{"id": "r1-124", "name": "124"}]),
                       {"group_names": {g124["key"]: "4-124"}, "create_groups": [g124["key"]]})
    g = by_ap(lay, "4-124")
    f += check("renamed away from the stale R1 group: a new group to create",
               not g["exists"] and g["create"] and g["name"] == "4-124", str((g["exists"], g["create"])))
    net = {"id": "n", "name": f"4-124@{SITE}", "ssid": f"4-124@{SITE}",
           "venueApGroups": [{"venueId": "v", "isAllApGroups": False, "apGroupIds": ["r1-x"]}]}
    lay = build_layout(snap, inventory([{"id": "r1-x", "name": "Unit 4-124"}], [net]))
    g = by_ap(lay, "4-124")
    f += check("matched by SSIDs: no options (its content decides, not its name)",
               g["matched_by"] == "SSIDs" and g["name_options"] == [] and not g["ambiguous"],
               str((g["matched_by"], g["name_options"])))

    print("\n6-7. Clashes and per-unit groups")
    stage = {"stage_unbroadcast": ["u999"]}
    lay = build_layout(snap, inventory(), stage)
    st = lay["staged"]["groups"]
    f += check("per-unit group for 9-999 built, with options",
               len(st) == 1 and st[0]["chosen"] and [n for n, _ in opts(st[0])] == ["999", "9-999"],
               str([(g["name"], opts(g)) for g in st]))
    f += check("no clash, not blocked", not lay["blocked"])
    lay = build_layout(snap, inventory(), {**stage, "group_names": {g124["key"]: "999"},
                                           "create_groups": [g124["key"]]})
    msgs = [w["message"] for w in lay["warnings"] if w["severity"] == "error"]
    f += check("unit group renamed onto the per-unit group's name: error, blocked",
               lay["blocked"] and any("999" in m and "more than one" in m for m in msgs), str(msgs))

    print(f"\n{'All checks passed' if not f else f'{f} check(s) failed'}")
    return f


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
