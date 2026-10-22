#!/usr/bin/env python3
"""
WLAN VLAN: one row per SZ WLAN, and when the R1 VLAN may be set.

WHAT THIS GUARDS

  1. Pairing follows the Migration Audit: name, then SSID; no match is
     blocked; R1 networks with no SZ WLAN are listed.
  2. Status: equal VLANs are in sync; a differing one is a change -- also
     when the network is active in other venues (the VLAN is tenant-wide;
     that is a note, counted from the query row's venueApGroups); an SZ
     VLAN pool is review; no SZ access VLAN, or an R1 VLAN pool, is blocked.
  3. The full GET carries no `venues` (verified live), so nothing about the
     venue may depend on it.
  4. SZ WLAN group overrides that differ from the default are noted.

Usage:
    docker compose exec backend python scripts/test_wlan_vlan.py

Exits non-zero on failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.wlan_vlan import build_rows

FAILS = 0
VENUE = "v1"


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


def sz(wid, name, vlan, ssid=None, pool=None, aaa=False):
    v = {"accessVlan": vlan, "aaaVlanOverride": aaa}
    if pool:
        v["vlanPooling"] = {"id": "p1", "name": pool}
    return {"id": wid, "name": name, "ssid": ssid or name, "vlan": v}


def r1(nid, name, vlan, ssid=None, pool=None, venues=(VENUE,)):
    """(query row, full GET) -- the GET has no `venues`, as R1 really returns it."""
    adv = {"vlanPool": {"name": pool}} if pool else {}
    row = {"id": nid, "name": name, "ssid": ssid or name,
           "venueApGroups": [{"venueId": v, "isAllApGroups": True} for v in venues]}
    full = {"id": nid, "name": name,
            "wlan": {"ssid": ssid or name, "vlanId": vlan, "advancedCustomization": adv}}
    return row, full


def run(sz_wlans, nets, groups=None):
    out = build_rows(sz_wlans, groups or [], [r for r, _ in nets], {f["id"]: f for _, f in nets}, VENUE)
    return out, {r["sz_name"]: r for r in out["rows"]}


def test_pairing_and_status():
    print("\n1-3. Pairing and status")
    out, by = run(
        [sz("a", "Same", 10), sz("b", "Diff", 20), sz("c", "BySsid", 30, ssid="ssid-c"),
         sz("d", "Nowhere", 40), sz("e", "Shared", 50), sz("f", "SzPool", 60, pool="pool-x"),
         sz("g", "NoVlan", None), sz("h", "R1Pool", 70)],
        [r1("n1", "Same", 10), r1("n2", "Diff", 1), r1("n3", "Other-name", 1, ssid="ssid-c"),
         r1("n5", "Shared", 1, venues=(VENUE, "v2", "v3")),
         r1("n6", "SzPool", 1), r1("n7", "NoVlan", 1), r1("n8", "R1Pool", 1, pool="r1-pool"),
         r1("n9", "R1Only", 5)],
    )
    check("equal VLAN is in sync", by["Same"]["status"] == "in_sync")
    check("differing VLAN is a change", by["Diff"]["status"] == "change")
    check("VLAN 1 -> SZ VLAN is a change, no notes",
          by["Diff"]["r1"]["vlan"] == 1 and not by["Diff"]["notes"], by["Diff"]["notes"])
    check("target is the SZ access VLAN", by["Diff"]["target_vlan"] == 20)
    check("SSID match pairs a renamed network",
          by["BySsid"]["r1_network_id"] == "n3" and by["BySsid"]["match_method"] == "ssid")
    check("no R1 match is blocked", by["Nowhere"]["status"] == "blocked")
    check("shared network is still a change", by["Shared"]["status"] == "change")
    check("shared note counts the other venues", "2 other venues" in by["Shared"]["notes"][0],
          by["Shared"]["notes"])
    check("SZ VLAN pool is review", by["SzPool"]["status"] == "review")
    check("no SZ access VLAN is blocked", by["NoVlan"]["status"] == "blocked")
    check("R1 VLAN pool is blocked", by["R1Pool"]["status"] == "blocked")
    check("R1-only network listed", [n["id"] for n in out["r1_only"]] == ["n9"], out["r1_only"])
    check("counts add up", sum(out["counts"].values()) == 8, out["counts"])


def test_sz_group_overrides():
    print("\n4. SZ WLAN group overrides")
    groups = [{"name": "wg-1", "members": [{"id": "a", "accessVlan": 300}]},
              {"name": "wg-2", "members": [{"id": "a", "accessVlan": 20}]}]
    _, by = run([sz("a", "Net", 20)], [r1("n1", "Net", 1)], groups)
    notes = " | ".join(by["Net"]["notes"])
    check("still a change", by["Net"]["status"] == "change")
    check("differing group override noted", "'wg-1' overrides it to VLAN 300" in notes, notes)
    check("group equal to default not noted", "wg-2" not in notes, notes)


if __name__ == "__main__":
    test_pairing_and_status()
    test_sz_group_overrides()
    print(f"\n{'ALL PASS' if not FAILS else f'{FAILS} FAILURE(S)'}")
    sys.exit(1 if FAILS else 0)
