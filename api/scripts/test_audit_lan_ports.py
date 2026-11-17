#!/usr/bin/env python3
"""
Migration audit: per-AP LAN ports, SZ vs R1 (services/sz_migration/audit_lan_ports.py).

The comparison itself is AP Port Grabber's (scripts/test_ap_port_grabber.py);
this guards the audit's wiring: feeding it from the SZ snapshot and the cached
R1 read, and the audit statuses.

WHAT THIS GUARDS

  1. The snapshot's per-AP `lan`, AP-Group models and profiles feed the
     comparison (AP override, group override and zone all resolve).
  2. Statuses: match / differs / pending (R1 can't read the AP yet) /
     not_in_r1 / unreadable (SZ AP detail failed).
  3. An SZ capture without ap_lan, or no R1 read, says why instead of
     reporting every AP as different.
  4. Scorecard row: pending only when nothing is comparable.

Usage:
    docker compose exec backend python scripts/test_audit_lan_ports.py
"""

import sys

sys.path.insert(0, ".")

from schemas.migration_audit import LanPortsAudit
from schemas.sz_migration import SZAPGroupEnriched, SZMigrationSnapshot, SZZoneSnapshot
from services.sz_migration.audit_lan_ports import build_lan_ports_audit
from services.sz_migration.audit_scorecard import lan_ports_category

FAILS = 0


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


CAP = {
    "model": "H550", "canSupportPoeMode": True, "poeModeCapabilities": ["Auto", "802.3at"],
    "canSupportPoeOut": True, "requireOneEnabledTrunkPort": True,
    "lanPorts": [
        {"id": "1", "defaultType": "ACCESS", "untagId": 1, "vlanMembers": "1", "supportDisable": True},
        {"id": "2", "defaultType": "ACCESS", "untagId": 1, "vlanMembers": "1", "supportDisable": True},
        {"id": "5", "defaultType": "TRUNK", "untagId": 1, "vlanMembers": "1-4094", "isPoePort": True,
         "supportDisable": False},
    ],
}
SZP = {
    "A": {"id": "A", "name": "Default Access Port", "type": "AccessPort", "untagId": 1, "vlanMembers": "1"},
    "T": {"id": "T", "name": "Default Trunk Port", "type": "TrunkPort", "untagId": 1, "vlanMembers": "1-4094"},
    "U": {"id": "U", "name": "Unit 30", "type": "AccessPort", "untagId": 30, "vlanMembers": "30"},
}


def lp(name, prof, enabled=True, **extra):
    return {"portName": name, "enabled": enabled, "ethPortProfile": {"id": prof, "name": SZP[prof]["name"]}, **extra}


ZONE = {"poeModeSetting": "Auto", "poeOutPortEnabled": False,
        "lanPorts": [lp("LAN1", "A"), lp("LAN2", "A"), lp("LAN5", "T")]}
VENUE_ENTRY = {"model": "H550", "poeMode": "Auto", "poeOut": False, "lanPorts": [
    {"portId": "1", "type": "ACCESS", "untagId": 1, "vlanMembers": "1", "enabled": True},
    {"portId": "2", "type": "ACCESS", "untagId": 1, "vlanMembers": "1", "enabled": True},
    {"portId": "5", "type": "TRUNK", "untagId": 1, "vlanMembers": "1-4094", "enabled": True},
]}
ON_VENUE = {"specific": {"useVenueSettings": True, "poeMode": "Auto", "poeOut": False}, "ports": {}}
NO_LAN = {"lanPorts": None, "poeModeSetting": None, "poeOutPortEnabled": None}


def sz_raw(n, group="g1"):
    # query/ap row shape (apGroupName absent, as on some vSZ versions)
    return {"apMac": f"AA:BB:CC:00:00:0{n}", "serial": f"S{n}", "deviceName": f"ap{n}", "model": "H550",
            "apGroupId": group}


def r1_ap(n):
    return {"serialNumber": f"S{n}", "macAddress": f"aa:bb:cc:00:00:0{n}", "name": f"r1-ap{n}",
            "model": "H550", "status": "2_00_Operational", "poePort": "4"}


def snapshot(ap_lan="default"):
    radio = {f"AA:BB:CC:00:00:0{n}": {"radios": {}, "lan": NO_LAN} for n in range(1, 7)}
    radio["AA:BB:CC:00:00:02"]["lan"] = {**NO_LAN, "lanPorts": [lp("LAN1", "U"), lp("LAN2", "A"), lp("LAN5", "T")]}
    radio["AA:BB:CC:00:00:05"] = {"radios": {}, "error": "HTTP 500"}
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone"),
        ap_groups=[SZAPGroupEnriched(id="g1", name="Units"), SZAPGroupEnriched(id="g2", name="Lobby")],
        aps=[sz_raw(1), sz_raw(2), sz_raw(3, "g2"), sz_raw(4), sz_raw(5), sz_raw(6)],
        ap_radio_config=radio,
        zone_ap_models={"H550": {"settings": ZONE, "eth_profiles": {"A": SZP["A"], "T": SZP["T"]}}},
        ap_lan={"group_models": {
            "g1|H550": None,
            "g2|H550": {"lanPorts": [lp("LAN1", "A"), lp("LAN2", "A", enabled=False), lp("LAN5", "T")]},
        }, "profiles": {"U": SZP["U"]}} if ap_lan == "default" else ap_lan,
    )


R1_LAN = {
    "r1_aps": [r1_ap(n) for n in (1, 2, 3, 4, 5)],          # ap6 not in the venue
    "caps": {"H550": CAP},
    "venue_entries": {"H550": VENUE_ENTRY},
    "ap_states": {"S1": ON_VENUE, "S2": ON_VENUE, "S3": ON_VENUE, "S5": ON_VENUE,
                  "S4": {"_error": "AP has not connected"}},
    "activations": {},
    "venue_profiles": {},
}

print("\nStatuses")
audit = build_lan_ports_audit(snapshot(), R1_LAN)
rows = {r.sz_name: r for r in audit.rows}
check("available", audit.available, audit.reason)
check("ap1: zone == venue -> match", rows["ap1"].status == "match" and rows["ap1"].source == "Zone",
      rows["ap1"])
check("ap2: per-AP 'Unit 30' from the snapshot's lan -> differs",
      rows["ap2"].status == "differs" and rows["ap2"].source == "AP override"
      and any("30" in c for c in rows["ap2"].changes), rows["ap2"])
check("ap3: AP Group 'Lobby' override (name from snapshot.ap_groups) -> differs",
      rows["ap3"].status == "differs" and rows["ap3"].source == "AP Group 'Lobby'", rows["ap3"])
check("ap4: R1 can't read the AP yet -> pending", rows["ap4"].status == "pending", rows["ap4"])
check("ap5: SZ AP detail failed -> unreadable", rows["ap5"].status == "unreadable", rows["ap5"])
check("ap6: not in the venue -> not_in_r1", rows["ap6"].status == "not_in_r1", rows["ap6"])
check("uplink LAN5 reported", rows["ap1"].uplink_ports == ["5"], rows["ap1"].uplink_ports)
check("counts", audit.counts == {"match": 1, "differs": 2, "pending": 1, "unreadable": 1, "not_in_r1": 1},
      audit.counts)
check("differences listed first", audit.rows[0].status == "differs")

print("\nNot comparable")
a = build_lan_ports_audit(snapshot(ap_lan=None), R1_LAN)
check("old SZ capture -> unavailable, says refresh SZ", not a.available and "refresh the SZ" in a.reason, a.reason)
a = build_lan_ports_audit(snapshot(), None)
check("no R1 read yet -> deferred, flags SZ's per-AP / group ports",
      not a.available and a.deferred and a.sz_sources == {"zone": 3, "ap": 1, "group": 1, "unknown": 1}
      and "1 AP(s) their own" in a.reason, (a.sz_sources, a.reason))
check("deferred -> scorecard not_checked with the SZ flag", lan_ports_category(a).status == "not_checked"
      and "not checked yet" in lan_ports_category(a).details[0])
a = build_lan_ports_audit(snapshot(), {"_error": "boom"})
check("R1 read failed -> unavailable", not a.available and "boom" in a.reason, a.reason)

print("\nScorecard")
cat = lan_ports_category(audit)
check("partial: 1 of 3 comparable match", (cat.status, cat.aligned_count, cat.r1_count) == ("partial", 1, 4),
      cat)
cat = lan_ports_category(LanPortsAudit(rows=[], counts={"pending": 5}))
check("only pending -> pending", cat.status == "pending", cat)
cat = lan_ports_category(LanPortsAudit(available=False, reason="why"))
check("unavailable -> not_checked with the reason", cat.status == "not_checked" and cat.details == ["why"], cat)

print("\nAll passed" if not FAILS else f"\nFAILED ({FAILS})")
sys.exit(1 if FAILS else 0)
