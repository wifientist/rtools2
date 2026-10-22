#!/usr/bin/env python3
"""
Migration audit, checkpoint 1: pairing SZ objects with R1 objects.

Every later comparison rests on these pairings, so a wrong pair silently
corrupts the whole report. What could go wrong:

  * SZ "default" AP Group has no R1 group by that name -- R1's default is
    unnamed. Matching by name reported it missing on every venue.
  * A network that exists in the tenant but isn't activated in the venue
    reads as "missing", sending someone to create a duplicate.
  * An SSID fallback that silently picks one of several networks.
  * One R1 object claimed by two SZ objects.
  * AP serial/MAC formats differ between controllers (case, separators).

WHAT THIS GUARDS

  1. WLAN: exact name > name ignoring case > SSID; method recorded.
  2. WLAN found only elsewhere in the tenant -> "not activated in venue".
  3. Ambiguous SSID fallback is noted; an R1 network is used at most once.
  4. R1-only networks and AP Groups are listed, not dropped.
  5. SZ "default" -> R1 default group (isDefault / unnamed).
  6. Empty SZ AP Group without an R1 match is noted, not counted missing.
  7. AP by serial, then MAC, across formats; presence and group alignment.
  8. The auditor uses the same pairings (no second opinion).

Usage:
    docker compose exec backend python scripts/test_audit_matching.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import (
    SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZAPGroupEnriched,
    SZWLANGroup, SZWLANGroupMember,
)
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.audit_matching import build_matching
from services.sz_migration.auditor import run_audit


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def snapshot():
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone"),
        wlans=[
            SZWLANFull(id="w-exact", name="Corp", ssid="Corp", auth_type="WPA2-PSK"),
            SZWLANFull(id="w-case", name="guest", ssid="Guest", auth_type="WPA2-PSK"),
            SZWLANFull(id="w-ssid", name="Old-Name", ssid="Residents", auth_type="WPA2-PSK"),
            SZWLANFull(id="w-tenant", name="Staff", ssid="Staff", auth_type="WPA2-PSK"),
            SZWLANFull(id="w-none", name="Gone", ssid="Gone", auth_type="WPA2-PSK"),
            SZWLANFull(id="w-dup", name="Residents-2", ssid="Residents", auth_type="WPA2-PSK"),
        ],
        wlan_groups=[SZWLANGroup(id="wg", name="default", members=[
            SZWLANGroupMember(id="w-exact", name="Corp", ssid="Corp")])],
        ap_groups=[
            SZAPGroupEnriched(id="sz-def", name="default", ap_count=2),
            SZAPGroupEnriched(id="sz-b", name="Building-B", ap_count=1),
            SZAPGroupEnriched(id="sz-empty", name="Unused", ap_count=0),
            SZAPGroupEnriched(id="sz-c", name="Building-C", ap_count=1),
        ],
        aps=[
            {"apMac": "0c:f4:d5:32:69:90", "serial": "ser001", "deviceName": "ap-1", "apGroupId": "sz-def", "model": "R510"},
            {"apMac": "0C-F4-D5-3D-58-60", "serial": None, "deviceName": "ap-2", "apGroupId": "sz-def"},
            {"apMac": "10:F0:68:0E:C6:70", "serial": "SER003", "deviceName": "ap-3", "apGroupId": "sz-b"},
            {"apMac": "AA:BB:CC:00:00:04", "serial": "SER004", "deviceName": "ap-4", "apGroupId": "sz-c"},
        ],
    )


def inventory():
    venue_nets = [
        {"id": "n-corp", "name": "Corp", "ssid": "Corp", "venueApGroups": []},
        {"id": "n-guest", "name": "Guest", "ssid": "Guest", "venueApGroups": []},
        {"id": "n-res1", "name": "Residents-A", "ssid": "Residents", "venueApGroups": []},
        {"id": "n-res2", "name": "Residents-B", "ssid": "Residents", "venueApGroups": []},
        {"id": "n-extra", "name": "R1-Only", "ssid": "R1-Only", "venueApGroups": []},
    ]
    tenant = [{k: n[k] for k in ("id", "name", "ssid")} for n in venue_nets] + [
        {"id": "n-staff", "name": "Staff", "ssid": "Staff"},
    ]
    return R1VenueInventory(
        venue_id="v", venue_name="V", tenant_id="t",
        wifi_networks=venue_nets, tenant_networks=tenant,
        wlan_groups=[SZWLANGroup(id="wg", name="default", members=[
            SZWLANGroupMember(id="w-exact", name="Corp", ssid="Corp")])],
        ap_groups=[
            {"id": "g-def", "name": "", "isDefault": True},
            {"id": "g-b", "name": "building-b"},
            {"id": "g-x", "name": "R1-Only-Group"},
        ],
        aps=[
            {"serialNumber": "SER001", "macAddress": "0CF4D5326990", "name": "ap-1", "apGroupId": "g-def"},
            {"serialNumber": "S2", "macAddress": "0c:f4:d5:3d:58:60", "name": "ap-2", "apGroupId": "g-b"},
            {"serialNumber": "SER999", "macAddress": "11:22:33:44:55:66", "name": "stranger", "apGroupId": "g-x"},
        ],
    )


def main():
    failures = 0
    print("Audit matching (checkpoint 1)")
    m = build_matching(snapshot(), inventory())
    w = {x.sz_wlan_id: x for x in m.wlans}

    # 1
    failures += check("exact name", w["w-exact"].r1_network_id == "n-corp" and w["w-exact"].method == "name")
    failures += check("name ignoring case", w["w-case"].r1_network_id == "n-guest" and w["w-case"].method == "name_case")
    failures += check("SSID fallback", w["w-ssid"].r1_network_id == "n-res1" and w["w-ssid"].method == "ssid")
    # 2
    failures += check("tenant-only network -> not activated in venue",
                      w["w-tenant"].r1_network_id == "n-staff" and not w["w-tenant"].activated_in_venue)
    failures += check("no counterpart -> none", w["w-none"].r1_network_id is None and w["w-none"].method == "none")
    # 3
    failures += check("ambiguous SSID noted", any("2 R1 networks" in n for n in w["w-ssid"].notes), str(w["w-ssid"].notes))
    failures += check("each R1 network used once", w["w-dup"].r1_network_id == "n-res2")
    # 4
    failures += check("R1-only networks listed", [n["id"] for n in m.r1_only_networks] == ["n-extra"])
    failures += check("R1-only AP Groups listed", [g["id"] for g in m.r1_only_ap_groups] == ["g-x"])

    g = {x.sz_name: x for x in m.ap_groups}
    # 5
    failures += check("SZ default -> R1 default group",
                      g["default"].r1_ap_group_id == "g-def" and g["default"].method == "default"
                      and g["default"].r1_name == "(default)")
    failures += check("named group ignoring case", g["Building-B"].r1_ap_group_id == "g-b")
    # 6
    failures += check("empty SZ group noted, not missing",
                      g["Unused"].r1_ap_group_id is None and g["Unused"].notes
                      and m.counts["ap_groups_unmatched"] == 1)

    aps = {a.name: a for a in m.aps}
    # 7
    failures += check("AP by serial across case", aps["ap-1"].method == "serial" and aps["ap-1"].presence == "in_both")
    failures += check("AP by MAC across separators", aps["ap-2"].method == "mac")
    failures += check("AP in the right group is aligned", aps["ap-1"].group_aligned is True)
    failures += check("AP in the wrong group is flagged",
                      aps["ap-2"].group_aligned is False and aps["ap-2"].expected_r1_ap_group == "(default)")
    failures += check("SZ AP not in venue -> sz_only", aps["ap-3"].presence == "sz_only")
    failures += check("SZ AP whose group has no R1 match -> no expected group",
                      aps["ap-4"].expected_r1_ap_group is None and aps["ap-4"].group_aligned is None)
    failures += check("R1 AP with no SZ counterpart -> r1_only", aps["stranger"].presence == "r1_only")
    failures += check("counts", m.counts["aps_in_both"] == 2 and m.counts["aps_sz_only"] == 2
                      and m.counts["aps_r1_only"] == 1 and m.counts["aps_wrong_group"] == 1, str(m.counts))

    # 8
    report = run_audit(snapshot(), inventory(), "sz", "r1", r1_network_details={})
    items = {n.sz_wlan_id: n for n in report.networks}
    failures += check("auditor uses the same pairings (case-insensitive name found)",
                      items["w-case"].actual_r1_id == "n-guest")
    failures += check("auditor says 'not activated here' instead of 'not found'",
                      "not activated" in items["w-tenant"].notes)
    default_cov = next(a for a in report.ap_group_activations if a.sz_ap_group_name == "default")
    failures += check("AP Group coverage finds the R1 default group", default_cov.r1_ap_group_found)

    # 10. top-of-report overview
    ov = {r.label: r for r in report.overview}
    failures += check("overview rows in order", [r.label for r in report.overview] ==
                      ["APs", "WLANs", "AP Groups", "Default AP Group — APs"], str(list(ov)))
    failures += check("APs 4 vs 3", (ov["APs"].sz_value, ov["APs"].r1_value, ov["APs"].status) == (4, 3, "differs"))
    failures += check("WLANs 6 vs 5, one more not activated here",
                      (ov["WLANs"].sz_value, ov["WLANs"].r1_value) == (6, 5) and "1 more" in ov["WLANs"].note)
    failures += check("AP Groups not one-to-one -> info",
                      (ov["AP Groups"].sz_value, ov["AP Groups"].r1_value, ov["AP Groups"].status) == (4, 3, "info")
                      and ov["AP Groups"].sz_note == "3 with APs")
    d = ov["Default AP Group — APs"]
    failures += check("default groups: SZ 'default' 2 APs vs R1 unnamed default 1 AP",
                      (d.sz_value, d.r1_value) == (2, 1) and "unnamed" in d.r1_note, str(d))

    # 9. scorecard
    sc = {c.key: c for c in report.scorecard}
    failures += check("scorecard has every category in order",
                      [c.key for c in report.scorecard] == ["venue_config", "aps", "ap_groups", "ap_membership",
                                                            "wlan_groups", "ssid_broadcast", "wlans", "wlan_config",
                                                            "passphrases"])
    failures += check("AP row: 4 SZ / 3 R1 / 2 in both, partial",
                      (sc["aps"].sz_count, sc["aps"].r1_count, sc["aps"].aligned_count, sc["aps"].status)
                      == (4, 3, 2, "partial"), str(sc["aps"]))
    failures += check("AP Group row counts only groups with APs",
                      sc["ap_groups"].sz_count == 3 and sc["ap_groups"].aligned_count == 2)
    failures += check("membership row: 1 of 2 in the expected group",
                      sc["ap_membership"].aligned_count == 1 and sc["ap_membership"].status == "partial")
    failures += check("WLAN row splits property-wide / per-unit",
                      any(b["label"] == "Property-wide" for b in sc["wlans"].breakdown), str(sc["wlans"].breakdown))
    failures += check("passphrases row without live R1 -> not_checked", sc["passphrases"].status == "not_checked")
    failures += check("venue settings row is a placeholder for now", sc["venue_config"].status == "not_checked")

    empty_r1 = inventory()
    empty_r1.aps = []
    sc2 = {c.key: c for c in run_audit(snapshot(), empty_r1, "sz", "r1", r1_network_details={}).scorecard}
    failures += check("no APs in R1 yet -> APs and membership are pending, not misaligned",
                      sc2["aps"].status == "pending" and sc2["ap_membership"].status == "pending")

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
