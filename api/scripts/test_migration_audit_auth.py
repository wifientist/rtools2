#!/usr/bin/env python3
"""
Migration audit: auth-path checks and the credential gate.

A WLAN that authenticated against an external server on SZ (usually
Cloudpath) lands in R1 one of two ways -- still pointing at that server, or
moved onto R1's own DPSK service -- and the audit has to recognise either as
legitimate and then check the one that was taken. The ways it could get that
wrong:

  * Calling a Cloudpath WLAN that became a DPSK network a "network_type"
    error. The mapper can only suggest one type; both are valid outcomes.

  * Passing an external network whose RADIUS profile points at a different
    server, or still carries the migration's placeholder secret. The second
    one rejects every request, and nothing else in the audit would show it.

  * Passing a DPSK network whose pool is empty, or has no identity group.
    Those exist and look fine by name, and nobody can connect.

  * Leaking. The audit reads objects that carry passphrases and shared
    secrets. None may reach the report, whatever path they take.

WHAT THIS GUARDS

  1. External path, same server -> OK, no counts claimed.
  2. External path, different server -> error.
  3. Placeholder shared secret still set -> error.
  4. Cloudpath WLAN moved to internal DPSK -> OK; the type change is not a diff.
  5. Internal path with an empty pool -> error.
  6. Internal pool with no identity group -> error.
  7. Neither linked -> path "none", status missing.
  8. Both linked -> hybrid, warning.
  9. A plain PSK WLAN gets no auth-path row.
 10. A passphrase that surfaces through a compared field is withheld.
 11. assert_clean() refuses a report that still contains a secret.
 12. The R1 count helpers and the probe return counts, never row content.

Usage:
    docker compose exec backend python scripts/test_migration_audit_auth.py

Exits non-zero on failure.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZReferencedObject
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.auditor import run_audit
from services.sz_migration.auth_probe import PLACEHOLDER_RADIUS_SECRET, collect_auth_facts
from services.sz_migration.audit_redaction import (
    CredentialLeakError, assert_clean, collect_secrets, scrub_report,
)
from r1api.services.dpsk import DpskService
from r1api.services.identity import IdentityService
from r1api.services.networks import NetworksService
from r1api.services.radius_profiles import RadiusProfileService

PSK = "correct-horse-battery"
SECRET = "radius-shared-s3cret"
ROW_PASSPHRASE = "pool-row-passphrase-99"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


# ==================== Builders ====================


def wlan(wid, name, auth_type, auth_service_id=None, acct=None, dpsk_external=False, raw=None):
    return SZWLANFull(
        id=wid, name=name, ssid=name, auth_type=auth_type,
        auth_service_id=auth_service_id, accounting_service_id=acct,
        external_dpsk={"enabled": True} if dpsk_external else None,
        raw=raw or {"name": name, "ssid": name},
    )


def auth_service(sid, ip, port=1812, secondary=None):
    raw = {"id": sid, "name": f"cloudpath-{sid}", "primary": {"ip": ip, "port": port}}
    if secondary:
        raw["secondary"] = {"ip": secondary, "port": port}
    return f"auth_service:{sid}", SZReferencedObject(
        ref_type="auth_service", id=sid, name=raw["name"], raw=raw
    )


def snapshot(wlans, refs=()):
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z-1", name="Zone"),
        wlans=wlans,
        referenced_objects=dict(refs),
    )


def inventory(networks, identity_groups=()):
    return R1VenueInventory(
        venue_id="v-1", venue_name="Venue", tenant_id="t-1",
        wifi_networks=networks, identity_groups=list(identity_groups),
    )


def net(nid, name, subtype):
    return {"id": nid, "name": name, "ssid": name, "nwSubType": subtype}


def radius_link(net_id, ip, port=1812, placeholder=False, ptype="AUTHENTICATION"):
    return {"id": f"rp-{net_id}-{ptype[:4]}", "name": f"rp-{net_id}", "type": ptype,
            "primary": f"{ip}:{port}", "secondary": None, "placeholder_secret": placeholder}


def pool(pid, passphrases, groups):
    return {"id": pid, "name": pid, "passphrase_count": passphrases,
            "identity_groups": [{"id": g, "name": g, "identity_count": c} for g, c in groups]}


def facts(radius=None, pools=None):
    return {
        "radius_by_network": radius or {},
        "radius_settings": {},
        "pools_by_network": {k: {"pools": v, "error": None} for k, v in (pools or {}).items()},
        "errors": [],
    }


def audit(snap, inv, f):
    return run_audit(snap, inv, "sz-job", "r1-job", r1_network_details={}, auth_facts=f)


def auth_row(report, wlan_id):
    return next((a for a in report.auth_paths if a.sz_wlan_id == wlan_id), None)


def errors(row):
    return [i.message for i in row.issues if i.severity == "error"]


# ==================== Fake R1 transport for the client helpers ====================


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status
        self.ok = 200 <= status < 300
        self.text = json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeTransport:
    """Answers the handful of R1 endpoints the probe reads, secrets included."""
    ec_type = "EC"

    def safe_json(self, response):
        return response.json()

    def get(self, path, params=None, override_tenant_id=None):
        if path.startswith("/radiusServerProfiles/"):
            pid = path.rsplit("/", 1)[1]
            secret = PLACEHOLDER_RADIUS_SECRET if pid == "rp-placeholder" else SECRET
            return Resp({"id": pid, "primary": {"ip": "10.0.0.5", "port": 1812,
                                                "sharedSecret": secret}})
        if path.endswith("/radiusServerProfileSettings"):
            return Resp({"enableAuthProxy": True, "enableAccountingProxy": False})
        if path.endswith("/dpskServices"):
            return Resp({"data": [{"id": "pool-1", "name": "pool-1"}]})
        if path.endswith("/identities"):
            return Resp({"content": [{"id": "i-1", "dpskPassphrase": ROW_PASSPHRASE}],
                         "totalElements": 42})
        return Resp({}, 404)

    def post(self, path, payload=None, params=None, override_tenant_id=None):
        if path == "/radiusServerProfiles/query":
            return Resp({"totalCount": 2, "data": [
                {"id": "rp-good", "name": "cp", "type": "AUTHENTICATION",
                 "primary": "10.0.0.5:1812", "wifiNetworkIds": ["n-ext"],
                 "sharedSecret": SECRET},
                {"id": "rp-placeholder", "name": "cp2", "type": "AUTHENTICATION",
                 "primary": "10.0.0.5:1812", "wifiNetworkIds": ["n-ph"]},
            ]})
        if path.endswith("/passphrases/query"):
            return Resp({"totalCount": 40, "data": [{"id": "p-1", "passphrase": ROW_PASSPHRASE}]})
        return Resp({}, 404)


class FakeR1:
    def __init__(self):
        t = FakeTransport()
        self.radius_profiles = RadiusProfileService(t)
        self.networks = NetworksService(t)
        self.dpsk = DpskService(t)
        self.identity = IdentityService(t)


# ==================== Tests ====================


async def main():
    failures = 0
    print("Migration audit: auth paths + credential gate")

    key, ref = auth_service("as-1", "10.0.0.5", secondary="10.0.0.6")

    # 1. external, same server
    s = snapshot([wlan("w1", "Corp", "WPA2-Enterprise", auth_service_id="as-1")], [(key, ref)])
    link = radius_link("n1", "10.0.0.5")
    link["secondary"] = "10.0.0.6:1812"
    r = audit(s, inventory([net("n1", "Corp", "aaa")]), facts(radius={"n1": [link]}))
    row = auth_row(r, "w1")
    failures += check("external path recognised", row and row.path.value == "external")
    failures += check("matching server -> ok", row and row.status.value == "ok",
                      f"issues: {[i.message for i in row.issues] if row else None}")
    failures += check("external path claims no identity counts",
                      row and row.identity_total is None and row.passphrase_total is None)

    # 2. external, different server
    r = audit(s, inventory([net("n1", "Corp", "aaa")]),
              facts(radius={"n1": [radius_link("n1", "192.168.9.9")]}))
    row = auth_row(r, "w1")
    failures += check("different server -> error",
                      row and any("differs" in m for m in errors(row)), str(row and errors(row)))

    r = audit(s, inventory([net("n1", "Corp", "aaa")]),
              facts(radius={"n1": [radius_link("n1", "10.0.0.5")]}))
    row = auth_row(r, "w1")
    failures += check("SZ secondary server with none in R1 -> warning",
                      row and row.status.value == "warning"
                      and any("secondary" in i.message for i in row.issues))

    # 3. placeholder secret
    r = audit(s, inventory([net("n1", "Corp", "aaa")]),
              facts(radius={"n1": [radius_link("n1", "10.0.0.5", placeholder=True)]}))
    row = auth_row(r, "w1")
    failures += check("placeholder secret -> error",
                      row and any("placeholder" in m for m in errors(row)))
    failures += check("network row is not OK when its auth path has an error",
                      r.networks[0].status.value == "warning")

    # 4. Cloudpath enterprise -> internal DPSK
    r = audit(s, inventory([net("n1", "Corp", "dpsk")]),
              facts(pools={"n1": [pool("pool-1", 40, [("ig-a", 30), ("ig-b", 10)])]}))
    row = auth_row(r, "w1")
    failures += check("internal path recognised", row and row.path.value == "internal")
    failures += check("counts summed", row and row.passphrase_total == 40 and row.identity_total == 40)
    failures += check("aaa -> dpsk is not a network_type diff",
                      not any(d.field == "network_type" for d in r.networks[0].diffs),
                      str(r.networks[0].diffs))
    failures += check("internal path with populated pool -> ok", row and row.status.value == "ok",
                      str(row and [i.message for i in row.issues]))

    # 5. empty pool
    r = audit(s, inventory([net("n1", "Corp", "dpsk")]),
              facts(pools={"n1": [pool("pool-1", 0, [("ig-a", 0)])]}))
    row = auth_row(r, "w1")
    failures += check("empty pool -> error", row and any("no passphrases" in m for m in errors(row)))

    # 6. pool without identity group
    r = audit(s, inventory([net("n1", "Corp", "dpsk")]),
              facts(pools={"n1": [pool("pool-1", 5, [])]}))
    row = auth_row(r, "w1")
    failures += check("pool with no identity group -> error",
                      row and any("no identity group" in m for m in errors(row)))

    # 7. neither
    r = audit(s, inventory([net("n1", "Corp", "aaa")]), facts())
    row = auth_row(r, "w1")
    failures += check("nothing linked -> path none, status missing",
                      row and row.path.value == "none" and row.status.value == "missing")

    # 8. hybrid
    r = audit(s, inventory([net("n1", "Corp", "dpsk")]),
              facts(radius={"n1": [radius_link("n1", "10.0.0.5")]},
                    pools={"n1": [pool("pool-1", 5, [("ig-a", 5)])]}))
    row = auth_row(r, "w1")
    failures += check("both linked -> hybrid warning",
                      row and row.path.value == "hybrid" and row.status.value == "warning")

    # SZ-internal DPSK that R1 turned into AAA keeps its type diff
    s2 = snapshot([wlan("w2", "Res", "DPSK")])
    r = audit(s2, inventory([net("n2", "Res", "aaa")]),
              facts(radius={"n2": [radius_link("n2", "10.0.0.5")]}))
    failures += check("SZ-internal DPSK -> AAA keeps the network_type error",
                      any(d.field == "network_type" for d in r.networks[0].diffs))

    # 9. PSK gets no auth row
    s3 = snapshot([wlan("w3", "Guest", "WPA2-PSK")])
    r = audit(s3, inventory([net("n3", "Guest", "psk")]), facts())
    failures += check("PSK WLAN has no auth-path row", not r.auth_paths)

    # 10. passphrase surfacing through a compared field is withheld
    leaky = wlan("w4", "Home", "WPA2-PSK", raw={
        "name": "Home", "ssid": "Home",
        "description": f"key is {PSK}",
        "encryption": {"method": "WPA2", "passphrase": PSK},
    })
    s4 = snapshot([leaky])
    r1_detail = {"n4": {"name": "Home", "description": PSK,
                        "wlan": {"ssid": "Home", "passphrase": PSK}}}
    r = run_audit(s4, inventory([net("n4", "Home", "psk")]), "sz", "r1",
                  r1_network_details=r1_detail, auth_facts=facts())
    secrets = collect_secrets([w.raw for w in s4.wlans], r1_detail)
    body, withheld = scrub_report(r.model_dump(mode="json"), secrets)
    failures += check("passphrase collected as a secret", PSK in secrets)
    failures += check("passphrase withheld from the report",
                      PSK not in json.dumps(body) and withheld >= 1,
                      f"withheld={withheld}")
    try:
        assert_clean(body, secrets)
        ok = True
    except CredentialLeakError:
        ok = False
    failures += check("scrubbed report passes the gate", ok)

    # 11. the gate refuses an unscrubbed leak
    try:
        assert_clean({"notes": f"x {PSK} y"}, {PSK})
        refused = False
    except CredentialLeakError as e:
        refused = PSK not in str(e)
    failures += check("assert_clean refuses a leak without echoing it", refused)
    try:
        assert_clean({"sharedSecret": "abc"}, set())
        refused = False
    except CredentialLeakError:
        refused = True
    failures += check("a string under a secret-named key is refused", refused)
    try:
        assert_clean({"passphrase_count": 12, "placeholder_secret": True,
                      "passphraseFormat": "MOST_SECURED"}, set())
        clean = True
    except CredentialLeakError:
        clean = False
    failures += check("counts, booleans and *Format fields are not flagged", clean)

    # 12. client helpers + probe return counts only
    r1 = FakeR1()
    failures += check("count_passphrases -> int",
                      await r1.dpsk.count_passphrases("pool-1") == 40)
    failures += check("count_identities_in_group -> int",
                      await r1.identity.count_identities_in_group("ig-a") == 42)
    failures += check("shared_secret_equals -> bool",
                      await r1.radius_profiles.shared_secret_equals("rp-good", "nope") is False
                      and await r1.radius_profiles.shared_secret_equals(
                          "rp-placeholder", PLACEHOLDER_RADIUS_SECRET) is True)
    rows = await r1.radius_profiles.query_radius_profiles()
    failures += check("RADIUS query rows drop undeclared fields",
                      all("sharedSecret" not in r for r in rows))

    f = await collect_auth_facts(
        r1, None,
        networks=[net("n-ext", "Corp", "aaa"), net("n-ph", "Corp2", "aaa"),
                  net("n-dpsk", "Res", "dpsk")],
        identity_groups=[{"id": "ig-a", "name": "ig-a", "dpskPoolId": "pool-1"}],
    )
    blob = json.dumps(f)
    failures += check("probe output carries no secret or passphrase",
                      SECRET not in blob and ROW_PASSPHRASE not in blob
                      and PLACEHOLDER_RADIUS_SECRET not in blob)
    failures += check("probe flags the placeholder profile",
                      f["radius_by_network"]["n-ph"][0]["placeholder_secret"] is True
                      and f["radius_by_network"]["n-ext"][0]["placeholder_secret"] is False)
    p = f["pools_by_network"]["n-dpsk"]["pools"][0]
    failures += check("probe counts pool + identities",
                      p["passphrase_count"] == 40 and p["identity_groups"][0]["identity_count"] == 42)
    failures += check("probe reads proxy setting",
                      f["radius_settings"]["n-ext"]["enable_auth_proxy"] is True)

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
