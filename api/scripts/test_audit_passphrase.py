#!/usr/bin/env python3
"""
Migration audit: does each R1 PSK network carry its SZ WLAN's passphrase?

Checked server-side and reported as a verdict only. The ways it could go
wrong:

  * leaking the passphrase -- or a fingerprint of it -- into the report;
  * failing on cached captures (the cache must not hold passphrases);
  * a WPA3 WLAN (SZ saePassphrase, migrated into R1's passphrase) read as
    "missing";
  * a fingerprint-key change read as "differs".

WHAT THIS GUARDS

  1. Fingerprints are keyed, deterministic, and don't contain the value.
  2. match / differs / missing / unknown verdicts, raw vs raw and raw vs
     cached fingerprint alike.
  3. SZ saePassphrase vs R1 passphrase compares as the same PSK.
  4. A different fingerprint key gives "unknown", not "differs".
  5. In a full audit: the verdict and a value-free diff are reported, and
     the report JSON holds no passphrase and no fingerprint.
  6. The cache stores fingerprints, and the credential gate ignores them.
  7. Mesh passphrase: "matches" row from fingerprints.

Usage:
    docker compose exec backend python scripts/test_audit_passphrase.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("FERNET_KEY", "test-key")
import services.sz_migration.passphrase_check as pc
from services.sz_migration.passphrase_check import compare_passphrases, fingerprint
from services.sz_migration.audit_cache import strip_credentials
from services.sz_migration.audit_redaction import collect_secrets
from services.sz_migration.auditor import run_audit
from services.sz_migration.venue_settings import compare_venue_settings
from schemas.sz_migration import SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull
from schemas.r1_inventory import R1VenueInventory

PSK = "unit-101-secret"
OTHER = "unit-102-secret"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def main():
    failures = 0
    print("Audit passphrase check")

    # 1
    a, b = fingerprint(PSK), fingerprint(PSK)
    failures += check("fingerprint is deterministic and keyed",
                      a == b and a.startswith("hmac-sha256:") and PSK not in a and fingerprint(OTHER) != a)
    failures += check("fingerprint passes through fingerprints", fingerprint(a) == a)

    # 2
    st = lambda s, r: compare_passphrases(s, r)["status"]
    failures += check("same raw values -> match", st({"passphrase": PSK}, {"passphrase": PSK}) == "match")
    failures += check("raw vs cached fingerprint -> match", st({"passphrase": fingerprint(PSK)}, {"passphrase": PSK}) == "match")
    failures += check("different -> differs", st({"passphrase": PSK}, {"passphrase": OTHER}) == "differs")
    failures += check("none in R1 -> missing_in_r1", st({"passphrase": PSK}, {}) == "missing_in_r1")
    failures += check("no R1 detail read -> unknown", st({"passphrase": PSK}, None) == "unknown")
    # 3
    failures += check("SZ saePassphrase vs R1 passphrase -> match",
                      st({"method": "WPA3", "saePassphrase": PSK}, {"passphrase": PSK}) == "match")
    # 4
    old = fingerprint(PSK)
    pc._key = lambda: b"rotated-key"
    failures += check("key changed -> unknown, not differs",
                      st({"passphrase": old}, {"passphrase": PSK}) == "unknown")
    pc._key = lambda: __import__("hashlib").sha256(b"rtools-audit-passphrase-fingerprint:test-key").digest()

    # 5
    snap = SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone"),
        wlans=[SZWLANFull(id="w1", name="U101", ssid="U101", auth_type="WPA2-PSK",
                          encryption={"method": "WPA2", "passphrase": PSK}, raw={"encryption": {"passphrase": PSK}}),
               SZWLANFull(id="w2", name="U102", ssid="U102", auth_type="WPA2-PSK",
                          encryption={"method": "WPA2", "passphrase": OTHER}, raw={"encryption": {"passphrase": OTHER}})],
    )
    inv = R1VenueInventory(venue_id="v", venue_name="V", tenant_id="t",
                           wifi_networks=[{"id": "n1", "name": "U101", "ssid": "U101", "nwSubType": "psk", "venueApGroups": [{"venueId": "v"}]},
                                          {"id": "n2", "name": "U102", "ssid": "U102", "nwSubType": "psk", "venueApGroups": [{"venueId": "v"}]}])
    details = {"n1": {"wlan": {"ssid": "U101", "passphrase": PSK}},
               "n2": {"wlan": {"ssid": "U102", "passphrase": "typo-" + OTHER}}}
    report = run_audit(snap, inv, "sz", "r1", r1_network_details=details)
    items = {n.sz_wlan_id: n for n in report.networks}
    failures += check("verdicts reported", items["w1"].passphrase_check == "match" and items["w2"].passphrase_check == "differs")
    failures += check("mismatch is a value-free error diff",
                      any(d.field == "passphrase" and d.actual == "different" and d.severity == "error" for d in items["w2"].diffs))
    blob = report.model_dump_json()
    failures += check("report holds no passphrase and no fingerprint",
                      PSK not in blob and OTHER not in blob and "hmac-sha256" not in blob)
    wc = next(c for c in report.scorecard if c.key == "wlan_config")
    failures += check("scorecard counts passphrase results",
                      any("1 match, 1 differ" in d for d in wc.details), str(wc.details))

    # 6
    stripped = strip_credentials(snap.model_dump(mode="json"))
    failures += check("cache stores fingerprints, not passphrases",
                      PSK not in json.dumps(stripped) and stripped["wlans"][0]["encryption"]["passphrase"] == fingerprint(PSK))
    failures += check("gate ignores fingerprints", not any(s.startswith("hmac-sha256:") for s in collect_secrets(stripped)))
    cached = SZMigrationSnapshot.model_validate(stripped)
    r2 = run_audit(cached, inv, "sz", "r1", r1_network_details=strip_credentials(details))
    failures += check("same verdicts from cached captures on both sides",
                      [n.passphrase_check for n in r2.networks] == ["match", "differs"])

    # 7
    zone = {"mesh": {"ssid": "m", "passphrase": PSK}}
    r1 = {"_errors": [], "mesh": {"enabled": True, "ssid": "m", "passphrase_set": True, "passphrase_fp": fingerprint(OTHER)}}
    rows = {r.label: r for r in compare_venue_settings(zone, {}, r1).rows}
    failures += check("mesh passphrase mismatch found from fingerprints",
                      rows["Mesh passphrase matches"].status == "differs")

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
