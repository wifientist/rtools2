#!/usr/bin/env python3
"""
WLAN Grabber: which SZ WLANs R1 is missing, and what each would become.

WHAT THIS GUARDS

  1. "Exists" is by name, tenant-wide (case-insensitive too); a network that
     only shares the SSID is a review note, never a match.
  2. PSK: security follows the SZ method (WPA2 / WPA3 / mixed); a missing or
     out-of-range passphrase blocks -- no placeholder is ever invented.
  3. No passphrase leaves the server: rows never carry one.
  4. Open + Portal, VLAN pools, external DPSK, legacy WPA are reviews; WEP
     and over-long names are blocked.
  5. DPSK: the pool most used by the venue's DPSK networks is suggested; with
     no suggestion the row is a review asking for a pool.
  6. RADIUS services are listed only for Enterprise WLANs still to create.

Usage:
    docker compose exec backend python scripts/test_wlan_grabber.py

Exits non-zero on failure.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.wlan_grabber import (
    BLOCKED, CREATE, EXISTS, REVIEW, build_rows, dpsk_pool_suggestion, radius_services,
)

FAILS = 0
PASS = "s3cret-pass-phrase"


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


def sz(wid, name, ssid=None, method="WPA2", passphrase=PASS, vlan=10, **extra):
    enc = {"method": method}
    if passphrase is not None:
        enc["passphrase"] = passphrase
    return {"id": wid, "name": name, "ssid": ssid or name, "encryption": enc,
            "vlan": {"accessVlan": vlan}, **extra}


def r1(nid, name, ssid=None, sub="psk", venues=()):
    return {"id": nid, "name": name, "ssid": ssid or name, "nwSubType": sub,
            "venueApGroups": [{"venueId": v} for v in venues]}


def by_id(result):
    return {r["sz_wlan_id"]: r for r in result["rows"]}


print("\n1. Exists is by name, tenant-wide")
res = by_id(build_rows(
    [sz("a", "Resident"), sz("b", "guest"), sz("c", "Lobby", ssid="Guest")],
    [r1("n1", "Resident", venues=["v9"]), r1("n2", "Guest"), r1("n3", "Other", ssid="Guest")],
    venue_id="v1",
))
check("exact name -> exists", res["a"]["status"] == EXISTS and res["a"]["r1"]["network_id"] == "n1")
check("exists elsewhere -> not activated in this venue noted",
      any("Not activated in this venue" in n for n in res["a"]["notes"]), res["a"]["notes"])
check("case-folded name -> exists", res["b"]["status"] == EXISTS and res["b"]["r1"]["network_id"] == "n2")
check("SSID-only match -> review, not exists", res["c"]["status"] == REVIEW and res["c"]["r1"] is None)
check("SSID note names both networks",
      any("'Guest'" in n and "'Other'" in n for n in res["c"]["notes"]), res["c"]["notes"])

print("\n2. PSK security and passphrase")
res = by_id(build_rows([
    sz("w2", "W2"), sz("w3", "W3", encryption={"method": "WPA3", "saePassphrase": PASS}),
    sz("mx", "MX", method="WPA23_MIXED"), sz("np", "NP", passphrase=None),
    sz("sh", "SH", passphrase="short"), sz("lg", "LG", method="WPA_Mixed"),
], []))
for k, want in (("w2", "WPA2"), ("mx", "WPA2/WPA3"), ("lg", "WPA2")):
    check(f"{k} security {want}", res[k]["security"] == want, res[k]["security"])
r = res["w3"]
check("WPA3 with saePassphrase -> psk WPA3, create",
      r["r1_type"] == "psk" and r["security"] == "WPA3" and r["status"] == CREATE, (r["security"], r["notes"]))
check("plain WPA2 -> create", res["w2"]["status"] == CREATE, res["w2"]["notes"])
check("no passphrase -> blocked", res["np"]["status"] == BLOCKED)
check("short passphrase -> blocked", res["sh"]["status"] == BLOCKED)
check("WPA/WPA2 mixed -> review", res["lg"]["status"] == REVIEW)

print("\n3. No passphrase in the rows")
blob = json.dumps(build_rows([sz("w2", "W2"), sz("w3", "W3", encryption={"method": "WPA3", "saePassphrase": PASS})], []))
check("passphrase never serialised", PASS not in blob and '"passphrase"' not in blob)

print("\n4. Other types")
res = by_id(build_rows([
    sz("op", "Open1", method="None", passphrase=None),
    sz("pt", "Portal", method="None", passphrase=None, portalServiceProfile={"id": "p1", "name": "GuestPortal"}),
    sz("vp", "Pooled", vlan=20) | {"vlan": {"accessVlan": 20, "vlanPooling": {"id": "vp1", "name": "Pool"}}},
    sz("wep", "Wep", method="WEP-64", passphrase=None),
    sz("ln", "N" * 33),
], []))
check("open -> create", res["op"]["r1_type"] == "open" and res["op"]["status"] == CREATE, res["op"]["notes"])
check("open + portal -> review", res["pt"]["r1_type"] == "open" and res["pt"]["status"] == REVIEW)
check("VLAN pool -> review, access VLAN kept", res["vp"]["status"] == REVIEW and res["vp"]["vlan_id"] == 20)
check("WEP -> blocked", res["wep"]["status"] == BLOCKED)
check("33-char name -> blocked", res["ln"]["status"] == BLOCKED)

print("\n5. DPSK pool suggestion")
sug = dpsk_pool_suggestion({
    "n1": [{"id": "p1", "name": "Units"}], "n2": [{"id": "p1", "name": "Units"}],
    "n3": [{"id": "p2", "name": "Staff"}],
}, "Venue A")
check("most-used pool suggested", sug and sug["pool_id"] == "p1" and "2 of 3" in sug["reason"], sug)
check("several pools noted", sug and "2 pools" in sug["reason"], sug)
check("no links -> no suggestion", dpsk_pool_suggestion({}, "V") is None)
dp = sz("d", "DPSK1", passphrase=None, dpsk={"enabled": True})
r = by_id(build_rows([dp], [], dpsk_suggestion=sug))["d"]
check("with suggestion -> create, pool preset",
      r["r1_type"] == "dpsk" and r["status"] == CREATE and r.get("suggested_dpsk_pool_id") == "p1", r)
r = by_id(build_rows([dp], []))["d"]
check("without suggestion -> review asking for a pool",
      r["status"] == REVIEW and any("Pick a DPSK pool" in n for n in r["notes"]), r["notes"])

print("\n6. RADIUS services")
ent = sz("e", "Corp", passphrase=None, authServiceOrProfile={"id": "rs1", "name": "NPS"})
done = sz("f", "Done", passphrase=None, authServiceOrProfile={"id": "rs2", "name": "Old"})
result = build_rows([ent, done], [r1("n", "Done", sub="aaa")])
rows = by_id(result)
check("enterprise -> aaa, create", rows["e"]["r1_type"] == "aaa" and rows["e"]["status"] == CREATE, rows["e"])
services = radius_services(result["rows"], {
    "auth_service:rs1": {"name": "NPS", "primary": {"ip": "10.0.0.5", "port": 1812}},
}, [{"id": "rp1", "name": "nps-r1", "type": "AUTHENTICATION", "primary": "10.0.0.5:1812",
     "secondary": None, "placeholder_secret": False}])
check("only the service of a WLAN still to create", [s["id"] for s in services] == ["rs1"], services)
check("same server -> suggests existing profile",
      services and services[0]["suggestion"]["action"] == "existing"
      and services[0]["suggestion"]["r1_profile_id"] == "rp1", services)

print(f"\n{'FAILED: ' + str(FAILS) if FAILS else 'All passed'}\n")
sys.exit(1 if FAILS else 0)
