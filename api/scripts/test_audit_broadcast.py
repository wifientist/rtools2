#!/usr/bin/env python3
"""
Migration audit, checkpoint 2: which SSIDs each AP broadcasts.

Shaped like a real per-unit site (Madera-Kessler Jersey Village: 609 APs,
ONE SZ AP Group, 600+ WLAN Groups): each unit's WLAN reaches its AP
through a per-AP WLAN Group override, so anything judged per AP Group says
nothing about what an AP actually broadcasts. On R1 the same intent is
per-unit AP Groups with the unit network activated on them.

WHAT THIS GUARDS

  1. SZ precedence per radio: AP override > AP Group override > zone > "default".
  2. A radio with WLAN service off broadcasts nothing; only the AP's own
     radios count; dual-5G uses lower/upper.
  3. AlwaysOff WLANs are left out.
  4. R1: AP -> its group -> networks on that group, plus All-AP-Groups networks.
  5. Per AP: missing/extra SSIDs; AP not in R1 = pending; unreadable SZ
     overrides on an otherwise matching AP = unknown.
  6. Per SSID coverage, including R1's own apCount.
  7. Broadcast groups: APs with the same SZ set, the R1 groups they sit in.
  8. Scorecard: SSID broadcast row; WLAN Groups row counts AP overrides;
     property-wide vs per-unit decided per AP.

Usage:
    docker compose exec backend python scripts/test_audit_broadcast.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import (
    SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZWLANGroup, SZWLANGroupMember,
    SZAPGroupEnriched, SZRadioConfig,
)
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.auditor import run_audit


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def wl(i, ssid, schedule=None):
    return SZWLANFull(id=i, name=ssid, ssid=ssid, auth_type="WPA2-PSK", schedule=schedule)


def wg(i, name, *members):
    return SZWLANGroup(id=i, name=name, members=[SZWLANGroupMember(id=m, name=m, ssid=m) for m in members])


def radios(r24=None, r5=None, off5=False, extra=None):
    d = {"radio24g": {"wlan_group_id": r24, "service_enabled": True},
         "radio5g": {"wlan_group_id": r5, "service_enabled": not off5}}
    d.update(extra or {})
    return {"dual5g": None, "radios": d}


def snapshot():
    wlans = [wl("Prop", "Prop"), wl("U101", "U101"), wl("U102", "U102"), wl("Dark", "Dark", {"type": "AlwaysOff"})]
    groups = [wg("wg-default", "default", "Prop", "U101", "U102", "Dark"),
              wg("wg-101", "wg-101", "Prop", "U101"),
              wg("wg-102", "wg-102", "Prop", "U102"),
              wg("wg-prop", "wg-prop", "Prop")]
    aps = [{"apMac": f"AA:00:00:00:00:0{i}", "serial": f"S{i}", "deviceName": f"ap{i}", "apGroupId": "apg-def"}
           for i in range(1, 7)]
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone", raw={"radioConfig": {"dual5gEnabled": False}}),
        wlans=wlans, wlan_groups=groups,
        ap_groups=[SZAPGroupEnriched(id="apg-def", name="default", ap_count=6,
                                     radio_config=SZRadioConfig(radio_5g="wg-prop"))],
        aps=aps,
        ap_radio_config={
            "AA:00:00:00:00:01": radios("wg-101", "wg-101"),            # unit 101 on both radios
            "AA:00:00:00:00:02": radios("wg-102", "wg-102"),            # unit 102
            "AA:00:00:00:00:03": radios(),                              # no AP override: 2.4 zone default, 5 AP Group wg-prop
            "AA:00:00:00:00:04": radios("wg-101", None, off5=True),     # 5 GHz service off
            "AA:00:00:00:00:05": {"error": "HTTPStatusError", "radios": {}},
            # ap6: not in R1
            "AA:00:00:00:00:06": radios("wg-101", "wg-101",
                                        extra={"radio6g": {"wlan_group_id": "wg-102", "service_enabled": True}}),
        },
    )


def inventory():
    nets = [
        {"id": "n-prop", "name": "Prop", "ssid": "Prop", "apCount": 5,
         "venueApGroups": [{"venueId": "v", "apGroupIds": ["g-01", "g-02", "g-def"], "isAllApGroups": False}]},
        {"id": "n-101", "name": "U101", "ssid": "U101",
         "venueApGroups": [{"venueId": "v", "apGroupIds": ["g-01"], "isAllApGroups": False}]},
        {"id": "n-102", "name": "U102", "ssid": "U102",
         "venueApGroups": [{"venueId": "v", "apGroupIds": ["g-02"], "isAllApGroups": False}]},
        {"id": "n-guest", "name": "Guest", "ssid": "Guest",
         "venueApGroups": [{"venueId": "v", "isAllApGroups": True}]},
    ]
    return R1VenueInventory(
        venue_id="v", venue_name="V", tenant_id="t", wifi_networks=nets,
        tenant_networks=[{k: n[k] for k in ("id", "name", "ssid")} for n in nets],
        ap_groups=[{"id": "g-def", "name": "", "isDefault": True}, {"id": "g-01", "name": "01"},
                   {"id": "g-02", "name": "02"}],
        aps=[{"serialNumber": "S1", "macAddress": "AA:00:00:00:00:01", "name": "ap1", "apGroupId": "g-01"},
             {"serialNumber": "S2", "macAddress": "AA:00:00:00:00:02", "name": "ap2", "apGroupId": "g-01"},  # wrong group
             {"serialNumber": "S3", "macAddress": "AA:00:00:00:00:03", "name": "ap3", "apGroupId": "g-def"},
             {"serialNumber": "S4", "macAddress": "AA:00:00:00:00:04", "name": "ap4", "apGroupId": "g-01"},
             {"serialNumber": "S5", "macAddress": "AA:00:00:00:00:05", "name": "ap5", "apGroupId": "g-01"}],
    )


def main():
    failures = 0
    print("Audit broadcast model (checkpoint 2)")
    report = run_audit(snapshot(), inventory(), "sz", "r1", r1_network_details={})
    bc = report.broadcast
    ap = {a.name: a for a in bc.aps}

    # 1
    failures += check("AP override wins on both radios", ap["ap1"].sz_ssids == ["Prop", "U101"], str(ap["ap1"].sz_ssids))
    failures += check("no AP override: 2.4 zone default + 5 GHz AP Group override",
                      ap["ap3"].sz_ssids == ["Prop", "U101", "U102"]
                      and any("5 GHz: AP Group override → wg-prop" in s for s in ap["ap3"].sz_sources)
                      and any("2.4 GHz: zone default → default" in s for s in ap["ap3"].sz_sources),
                      str(ap["ap3"].sz_sources))
    # 2
    failures += check("5 GHz service off contributes nothing",
                      ap["ap4"].sz_ssids == ["Prop", "U101"] and any("service off" in s for s in ap["ap4"].sz_sources))
    failures += check("an AP's own 6 GHz radio counts", "U102" in ap["ap6"].sz_ssids)
    # 3
    failures += check("AlwaysOff WLAN left out", "Dark" not in ap["ap3"].sz_ssids)
    # 4 + 5
    failures += check("R1: group networks + All-AP-Groups", ap["ap1"].r1_ssids == ["Guest", "Prop", "U101"])
    failures += check("only the All-AP-Groups SSID is extra on a correct AP",
                      ap["ap1"].missing_in_r1 == [] and ap["ap1"].extra_in_r1 == ["Guest"] and ap["ap1"].status == "differs")
    failures += check("AP in the wrong R1 group: unit SSID missing, other unit's extra",
                      ap["ap2"].missing_in_r1 == ["U102"] and "U101" in ap["ap2"].extra_in_r1)
    failures += check("AP not in R1 -> pending", ap["ap6"].status == "pending")
    failures += check("unreadable SZ overrides flagged",
                      any("no readable per-AP radio config" in n for n in bc.notes))
    # 6
    cov = {s.ssid: s for s in bc.ssids}
    # ap5 (per-AP read failed) inherits the AP Group, so it carries U101 too
    failures += check("SSID coverage counts", (cov["U101"].sz_ap_count, cov["U101"].r1_ap_count) == (5, 4)
                      and cov["U102"].missing_in_r1 == 3 and cov["U102"].status == "missing",
                      str(cov["U101"]) + str(cov["U102"]))
    failures += check("R1-only SSID marked extra", cov["Guest"].status == "extra" and cov["Guest"].sz_ap_count == 0)
    failures += check("R1's own apCount carried", cov["Prop"].r1_reported_ap_count == 5)
    # 7
    groups = {tuple(g.ssids): g for g in bc.groups}
    g101 = groups.get(("Prop", "U101"))
    failures += check("broadcast group for unit 101 found in R1 group 01",
                      g101 is not None and g101.r1_ap_groups.get("01") and "01" not in g101.r1_groups_matching_ssids,
                      str(g101))
    # 8
    sc = {c.key: c for c in report.scorecard}
    failures += check("scorecard has the SSID-broadcast row", "ssid_broadcast" in sc
                      and sc["ssid_broadcast"].sz_count == 5 and sc["ssid_broadcast"].aligned_count == 0
                      and not any("(," in d for d in sc["ssid_broadcast"].details), str(sc.get("ssid_broadcast")))
    failures += check("WLAN Groups row counts AP-level overrides",
                      any("by AP override: 2 (on 4 APs)" in d for d in sc["wlan_groups"].details), str(sc["wlan_groups"].details))
    scopes = {b["label"]: b["sz"] for b in sc["wlans"].breakdown}
    failures += check("property-wide vs per-unit decided per AP",
                      scopes.get("Property-wide") == 1 and scopes.get("Per-unit") == 2, str(sc["wlans"].breakdown))

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
