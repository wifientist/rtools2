#!/usr/bin/env python3
"""
SZ -> R1 migration: captive-portal (hotspot) WLANs.

Murabella Guest -- an SZ Hotspot WLAN on portal "guest", RADIUS auth
"radius2" -- was migrated as a plain Open network: the extraction asked for
the portal at /portals/guest (a hotspot portal lives at /portals/hotspot) and
got an error, and the mapper turned every "Open + Portal" into "open".

WHAT THIS GUARDS
  1. The mapper: SZ Hotspot (and Hotspot_MacByPass) -> "wispr"; a guest /
     web-auth portal still -> open, with a note.
  2. The extraction reads a hotspot WLAN's portal from /portals/hotspot,
     falling back to /portals/guest (and the other way round).
  3. create_networks builds the WISPr guest network exactly as WLAN Grabber
     does: portal + redirect URL, provider, walled garden, sessions, the SZ
     signing key as integration key, the plan's R1 name with SZ's SSID,
     SZ's own description (none when SZ has none); then links the RADIUS
     auth profile mapped from the SZ auth service, with SZ's proxy setting.
  4. A WISPr network with RADIUS auth and no mapped profile fails (it would
     authenticate nobody) instead of being created half-built.
  5. The portal signing / integration keys are credentials: redacted.

Usage:
    docker compose exec backend python scripts/test_migration_wispr.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZReferencedObject
from services.sz_migration.mapper import map_wlan_to_r1_type
from services.sz_migration.audit_redaction import is_secret_key
import services.sz_migration.extractor as extractor
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.create_networks as cn

KEY = "AbCdEf0123456789"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def hotspot_raw(**over):
    return {"id": "g", "name": "Murabella Guest", "ssid": "Murabella Guest", "type": "Hotspot",
            "encryption": {"method": "None"}, "vlan": {"accessVlan": 1004}, "bypassCNA": False,
            "portalServiceProfile": {"id": "hp1", "name": "guest"},
            "authServiceOrProfile": {"id": "rs", "name": "radius2", "throughController": True}, **over}


PORTAL = {"id": "hp1", "name": "guest", "portalType": "External",
          "portalUrl": "http://captive-portal.example/captive_guest.html",
          "redirect": {"url": "http://captive-portal.example/redirect.html"},
          "walledGardens": ["*.xfinity.com", "*.comcast.net"],
          "userSession": {"timeoutInMin": 1440, "gracePeriodInMin": 60},
          "signatureSigningKey": KEY}


def full(raw):
    return SZWLANFull(id=raw["id"], name=raw["name"], ssid=raw["ssid"], auth_type="Open + Portal", raw=raw,
                      auth_service_id=(raw.get("authServiceOrProfile") or {}).get("id"))


def test_mapper():
    print("\n1. Mapper")
    f = 0
    f += check("SZ Hotspot -> wispr", map_wlan_to_r1_type(full(hotspot_raw())).r1_network_type == "wispr")
    f += check("Hotspot_MacByPass -> wispr",
               map_wlan_to_r1_type(full(hotspot_raw(type="Hotspot_MacByPass"))).r1_network_type == "wispr")
    g = map_wlan_to_r1_type(full(hotspot_raw(type="Guest")))
    f += check("a guest / web-auth portal -> open, with a note", g.r1_network_type == "open" and "not copied" in g.notes)
    return f


def test_extraction():
    print("\n2. Portal read from the right endpoint")
    f = 0
    calls = []

    class SZ:
        api_version = "v11_1"

        async def _request(self, method, path):
            calls.append(path.split("/portals/")[1].split("/")[0])
            if "/portals/hotspot/" in path:
                return dict(PORTAL)
            raise RuntimeError("404")

    raw = asyncio.run(extractor._get_portal(SZ(), "z", "hp1", hotspot=True))
    f += check("hotspot WLAN's portal: /portals/hotspot first", calls == ["hotspot"] and raw["portalUrl"], str(calls))
    calls.clear()
    raw = asyncio.run(extractor._get_portal(SZ(), "z", "hp1", hotspot=False))
    f += check("guest first, falls back to hotspot", calls == ["guest", "hotspot"] and raw["_portal_kind"] == "hotspot",
               str(calls))
    return f


class FakeRedis:
    def __init__(self, snap):
        self.snap = snap

    async def get(self, k):
        return self.snap.model_dump_json()


def _snapshot(portal_raw):
    raw = hotspot_raw(description=None)
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone"), wlans=[full(raw)],
        referenced_objects={"portal_service_profile:hp1": SZReferencedObject(
            ref_type="portal_service_profile", id="hp1", name="guest", raw=portal_raw)})


def _phase(snapshot, mappings):
    calls = {"created": [], "linked": [], "radius_settings": []}

    async def create_from_payload(tenant_id, payload):
        calls["created"].append(payload)
        return {"id": "net-1"}

    async def link(network_id, radius_profile_id, tenant_id):
        calls["linked"].append(radius_profile_id)

    async def settings(net_id, s, tenant_id):
        calls["radius_settings"].append(s)

    r1 = SimpleNamespace(
        networks=SimpleNamespace(create_wifi_network_from_payload=create_from_payload,
                                 update_radius_settings_on_network=settings),
        radius_profiles=SimpleNamespace(link_radius_to_network=link))
    job = SimpleNamespace(input_data={"sz_snapshot_job_id": "s"},
                          global_phase_results={"create_radius_profiles": {"radius_profile_mappings": mappings}})

    async def get_job(_):
        return job
    p = cn.CreateNetworksPhase(PhaseContext(job_id="j", r1_client=r1, venue_id="v", tenant_id="t", options={},
                                            state_manager=SimpleNamespace(get_job=get_job)))

    async def emit(*a, **k):
        return None

    async def track(*a, **k):
        return None
    p.emit, p.track_resource = emit, track

    async def fake_redis():
        return FakeRedis(snapshot)
    cn.get_redis_client = fake_redis
    return p, calls


def test_create():
    print("\n3-4. create_networks builds the WISPr network")
    f = 0
    p, calls = _phase(_snapshot(PORTAL), {"rs": "r1-radius2"})
    extra = {"sz_wlan_id": "g", "r1_network_type": "wispr", "sz_description": None}
    out = asyncio.run(p._create_wispr(extra, "Murabella Guest_Site", "Murabella Guest"))
    body = calls["created"][0] if calls["created"] else {}
    gp, wlan = body.get("guestPortal") or {}, body.get("wlan") or {}
    f += check("created as an R1 guest (WISPr) network", body.get("type") == "guest"
               and gp.get("guestNetworkType") == "WISPr" and out.network_id == "net-1", str(body)[:300])
    f += check("portal / redirect URL, provider, walled garden, sessions from SZ",
               gp.get("externalPortalUrl") == PORTAL["portalUrl"] and gp.get("redirectUrl") == PORTAL["redirect"]["url"]
               and gp["wisprPage"]["externalProviderName"] == "guest"
               and gp.get("walledGardens") == PORTAL["walledGardens"]
               and gp.get("userSessionTimeout") == 1440 and gp.get("userSessionGracePeriod") == 60, str(gp)[:300])
    f += check("SZ signing key -> integration key, MAC/IP encrypted",
               gp["wisprPage"].get("integrationKey") == KEY and gp["wisprPage"]["encryptMacIpEnabled"] is True)
    f += check("the plan's R1 name; SSID and VLAN exactly SZ's; no made-up description",
               body.get("name") == "Murabella Guest_Site" and wlan.get("ssid") == "Murabella Guest"
               and wlan.get("vlanId") == 1004 and "description" not in body, str(body.get("name")))
    f += check("RADIUS auth + 'radius2' profile linked, proxied as SZ",
               gp["wisprPage"]["authType"] == "RADIUS" and calls["linked"] == ["r1-radius2"]
               and calls["radius_settings"] == [{"enableAuthProxy": True}], str(calls))

    p, calls = _phase(_snapshot(PORTAL), {})
    try:
        asyncio.run(p._create_wispr(extra, "Murabella Guest", "Murabella Guest"))
        raised = None
    except RuntimeError as e:
        raised = str(e)
    f += check("RADIUS auth but no mapped profile: fails, nothing created",
               raised and "RADIUS profile" in raised and not calls["created"], str(raised))

    p, calls = _phase(_snapshot({"id": "hp1", "_error": "404"}), {"rs": "r1-radius2"})
    try:
        asyncio.run(p._create_wispr(extra, "Murabella Guest", "Murabella Guest"))
        raised = None
    except RuntimeError as e:
        raised = str(e)
    f += check("portal unreadable in the snapshot: fails with the reason, nothing created",
               raised and "portal" in raised.lower() and not calls["created"], str(raised))
    return f


def test_secrets():
    print("\n5. Portal keys are credentials")
    f = 0
    f += check("signatureSigningKey / integrationKey / externalPortalKey redacted",
               all(is_secret_key(k) for k in ("signatureSigningKey", "integrationKey", "externalPortalKey"))
               and not any(is_secret_key(k) for k in ("portalUrl", "passphraseFormat", "wisprPage")))
    return f


if __name__ == "__main__":
    failures = test_mapper() + test_extraction() + test_create() + test_secrets()
    print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
    sys.exit(1 if failures else 0)
