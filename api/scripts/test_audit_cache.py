#!/usr/bin/env python3
"""
Migration-audit capture cache.

A week-long cache of SZ and R1 captures saves minutes per run on a large
site -- but those captures carry every WLAN passphrase (SZ raw, R1 network
detail) and shared secrets. What could go wrong:

  * passphrases sitting in Redis for 7 days;
  * stripping them changes audit results (e.g. "mesh passphrase set" flips);
  * the "present" marker itself treated as a secret by the credential gate;
  * a capture taken by an older audit (no per-AP radio reads) silently used;
  * reads extending the TTL so a capture never expires.

WHAT THIS GUARDS

  1. SZ round trip: same snapshot back, every credential replaced by the marker.
  2. R1 round trip: inventory, network details, auth facts, venue settings back,
     credentials stripped.
  3. "Set or not" survives stripping (mesh passphrase comparison unchanged).
  4. The marker is not collected as a secret by the gate.
  5. An older capture version reads as stale with a reason, and isn't returned.
  6. 7-day TTL from capture; reading doesn't extend it; clear removes it.

Usage:
    docker compose exec backend python scripts/test_audit_cache.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import services.sz_migration.audit_cache as cache
from services.sz_migration.audit_redaction import collect_secrets, CREDENTIAL_PRESENT
from services.sz_migration.venue_settings import compare_venue_settings
from schemas.sz_migration import SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull

PSK = "unit-101-passphrase"
MESH = "mesh-passphrase-xyz"
R1_PSK = "r1-network-passphrase"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


class FakeRedis:
    def __init__(self):
        self.h, self.ttls, self.ttl_calls = {}, {}, 0

    async def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    async def ttl(self, k):
        self.ttl_calls += 1
        return self.ttls.get(k, -2)

    async def delete(self, *keys):
        n = sum(1 for k in keys if k in self.h)
        for k in keys:
            self.h.pop(k, None)
            self.ttls.pop(k, None)
        return n

    def pipeline(self, transaction=True):
        outer = self

        class P:
            def __init__(self):
                self.ops = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def delete(self, k):
                self.ops.append(("del", k))

            def hset(self, k, mapping):
                self.ops.append(("hset", k, mapping))

            def expire(self, k, s):
                self.ops.append(("exp", k, s))

            async def execute(self):
                for op in self.ops:
                    if op[0] == "del":
                        outer.h.pop(op[1], None)
                    elif op[0] == "hset":
                        outer.h.setdefault(op[1], {}).update(op[2])
                    else:
                        outer.ttls[op[1]] = op[2]
        return P()


async def main():
    failures = 0
    print("Audit capture cache")
    fake = FakeRedis()

    async def get():
        return fake

    cache.get_audit_cache_redis = get

    snap = SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone", raw={"mesh": {"ssid": "m", "passphrase": MESH}}),
        wlans=[SZWLANFull(id="w", name="U101", ssid="U101", auth_type="WPA2-PSK",
                          encryption={"method": "WPA2", "passphrase": PSK},
                          raw={"encryption": {"passphrase": PSK}})],
        ap_radio_config={"AA": {"radios": {"radio24g": {"wlan_group_id": "g", "service_enabled": True}}}},
    )

    # 1
    meta = await cache.save_sz(3, "z", snap)
    stored = json.dumps(fake.h)
    failures += check("no SZ passphrase in Redis", PSK not in stored and MESH not in stored)
    back, m = await cache.load_sz(3, "z")
    failures += check("SZ round trip keeps the data",
                      back and back.wlans[0].ssid == "U101" and back.ap_radio_config == snap.ap_radio_config
                      and m["zone_name"] == "Zone" and m["stale_reason"] is None)
    failures += check("passphrase replaced by its keyed fingerprint",
                      back.wlans[0].encryption["passphrase"].startswith("hmac-sha256:"))

    # 2
    bundle = {"inventory": {"venue_id": "v", "venue_name": "V", "tenant_id": "t",
                            "wifi_networks": [{"id": "n", "ssid": "U101"}], "aps": []},
              "network_details": {"n": {"wlan": {"passphrase": R1_PSK, "ssid": "U101"}}},
              "auth_facts": {"radius_by_network": {}}, "venue_settings": {"mesh": {"passphrase_set": True}}}
    await cache.save_r1(9, "ec-1", "v", bundle)
    failures += check("no R1 passphrase in Redis", R1_PSK not in json.dumps(fake.h))
    rb, rm = await cache.load_r1(9, "ec-1", "v")
    failures += check("R1 round trip keeps every part",
                      rb and set(rb) == {"inventory", "network_details", "auth_facts", "venue_settings"}
                      and rb["network_details"]["n"]["wlan"]["ssid"] == "U101" and rm["venue_name"] == "V")
    failures += check("tenant is part of the key (MSP)", await cache.load_r1(9, "ec-2", "v") == (None, None))

    # 3
    rows = {(r.section, r.label): r for r in compare_venue_settings(
        back.zone.raw, {}, {"_errors": [], "mesh": {"enabled": True, "ssid": "m", "passphrase_set": True}}).rows}
    failures += check("'mesh passphrase set' unchanged after stripping",
                      rows[("Mesh", "Mesh passphrase set")].status == "match")

    # 4
    failures += check("marker is not treated as a secret",
                      CREDENTIAL_PRESENT not in collect_secrets(back.model_dump(mode="json")))

    # 5
    key = cache.sz_key(3, "z")
    m_old = json.loads(fake.h[key]["meta"]) | {"version": 1}
    fake.h[key]["meta"] = json.dumps(m_old)
    stale, sm = await cache.load_sz(3, "z")
    failures += check("older capture version -> stale, not returned",
                      stale is None and sm and "older version" in (sm["stale_reason"] or ""))

    # 6
    failures += check("7-day TTL", fake.ttls[cache.r1_key(9, "ec-1", "v")] == 7 * 24 * 3600)
    before = dict(fake.ttls)
    await cache.load_r1(9, "ec-1", "v")
    failures += check("reading doesn't extend the TTL", fake.ttls == before)
    st = await cache.status(3, "z", 9, "ec-1", "v")
    failures += check("status reports both sides", st["sz"] and st["r1"] and st["ttl_days"] == 7)
    failures += check("clear removes it", await cache.clear(cache.r1_key(9, "ec-1", "v")) == 1
                      and await cache.load_r1(9, "ec-1", "v") == (None, None))

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
