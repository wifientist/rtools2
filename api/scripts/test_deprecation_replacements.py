"""
The R1 call replacements of 2026-11-11 (deprecation batches 1-3): the logic
that live checks cannot cover on their own. No R1.

    docker compose exec backend python scripts/test_deprecation_replacements.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures += 1


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300
        self.content = b"" if body is None else b"x"
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class Fake:
    """Answers by (method, path) from a table; records every call."""
    ec_type = "MSP"

    def __init__(self, table):
        self.table, self.calls = table, []

    def _answer(self, method, path, **kw):
        self.calls.append((method, path, kw))
        for (m, p), fn in self.table.items():
            if m == method and (p == path or (p.endswith("*") and path.startswith(p[:-1]))):
                return fn(path, **kw) if callable(fn) else fn
        return Resp(404, {"message": "no route"})

    def get(self, path, params=None, override_tenant_id=None, accept=None):
        return self._answer("get", path, params=params, accept=accept)

    def post(self, path, payload=None, params=None, override_tenant_id=None):
        return self._answer("post", path, payload=payload)

    def put(self, path, payload=None, override_tenant_id=None, content_type=None):
        return self._answer("put", path, payload=payload)

    def safe_json(self, r):
        return r.json()

    async def await_task_completion(self, *a, **k):
        return {"status": "SUCCESS"}


def paged(rows, one_indexed=True, size_key="pageSize"):
    def fn(path, payload=None, **kw):
        page, size = payload.get("page", 1), payload.get(size_key, 25)
        start = (page - (1 if one_indexed else 0)) * size
        return Resp(200, {"data": rows[start:start + size], "totalCount": len(rows)})
    return fn


print("1. paged readers")
from r1api.services.venues import VenueService  # noqa: E402
groups = [{"id": f"g{i}", "name": f"G{i}", "venueId": "v", "apSerialNumbers": [f"s{i}"]} for i in range(2500)]
f = Fake({("post", "/venues/apGroups/query"): paged(groups)})
rows = VenueService(f).query_all_ap_groups("t")
check("AP groups: every page read, 1-indexed, pageSize sent", len(rows) == 2500 and len({r["id"] for r in rows}) == 2500
      and all(c[2]["payload"]["pageSize"] == 1000 for c in f.calls))
check("AP groups: `aps` rebuilt from apSerialNumbers for older readers",
      rows[0]["aps"] == [{"serialNumber": "s0", "apGroupId": "g0", "venueId": "v"}])
f = Fake({("post", "/venues/apGroups/query"): paged(groups)})
VenueService(f).query_all_ap_groups("t", "v")
check("AP groups: venue filter sent", f.calls[0][2]["payload"]["filters"] == {"venueId": ["v"]})
venues = [{"id": f"v{i}", "name": f"V{i}"} for i in range(1200)]
f = Fake({("post", "/venues/query"): paged(venues)})
got = asyncio.run(VenueService(f).get_venues("t"))
check("venues: all 1,200 over 3 pages, old address shape rebuilt",
      len(got) == 1200 and isinstance(got[0]["address"], dict) and len(f.calls) == 3)

from r1api.services.entitlements import EntitlementsService  # noqa: E402
blocks = [{"externalId": f"e{i}", "sku": "S", "quantity": 1} for i in range(1500)]
f = Fake({("post", "/entitlements/query"): paged(blocks)})
check("entitlements: all pages", len(EntitlementsService(f).query_entitlements()) == 1500)
pool = EntitlementsService._normalize_pool([{"externalId": "e1", "licenseType": "APSW", "sku": "S", "quantity": 5,
                                             "effectiveDate": "Mon Dec 01 00:00:00 UTC 2025",
                                             "expirationDate": "Thu Jan 01 23:59:59 UTC 2026"}])
check("pool block: externalId as id, licenseType as device type, Java dates parsed",
      pool[0]["id"] == "e1" and pool[0]["device_type"] == "APSW" and pool[0]["term_days"] == 31, str(pool))

from services.pisr import fetch as pf  # noqa: E402
sets = [{"id": f"s{i}"} for i in range(1200)]
def spring(rows):
    def fn(path, payload=None, **kw):
        page, size = payload["page"], payload["pageSize"]
        return Resp(200, {"content": rows[page * size:(page + 1) * size], "paging": {"totalCount": len(rows)}})
    return fn
f = Fake({("post", "/policySets/query"): spring(sets)})
got = pf.policy_sets(f, "t")
check("policy sets: 0-indexed, every page (the GET stopped at 10)",
      len(got) == 1200 and f.calls[0][2]["payload"]["page"] == 0, len(got))

print("2. RADIUS profiles keep their secrets")
from r1api.services.radius_profiles import RadiusProfileService, PROFILE_MEDIA_TYPE  # noqa: E402
f = Fake({("post", "/radiusServerProfiles/query"): paged([{"id": "r1"}, {"id": "r2"}]),
          ("get", "/radiusServerProfiles/r1"): Resp(200, {"id": "r1", "primary": {"ip": "1.1.1.1", "port": 1812, "sharedSecret": "x"}}),
          ("get", "/radiusServerProfiles/r2"): Resp(404, {})})
rows = RadiusProfileService(f).get_radius_profiles("t")
check("ids from the query, full rows (with sharedSecret) by id, deleted one skipped",
      [r["id"] for r in rows] == ["r1"] and rows[0]["primary"]["sharedSecret"] == "x")
check("per-profile read pinned to v1.1",
      all(c[2].get("accept") == PROFILE_MEDIA_TYPE for c in f.calls if c[0] == "get"))

print("3. PISR DHCP pools joined from the service profile")
f = Fake({("post", "/dhcpConfigServiceProfiles/query"): Resp(200, {"data": [{"id": "d1", "venueIds": ["v"]}]}),
          ("get", "/dhcpConfigServiceProfiles/d1"): Resp(200, {"dhcpPools": [
              {"name": "Guests", "vlanId": 20, "subnetAddress": "10.0.0.0", "startIpAddress": "10.0.0.10"},
              {"name": "Spare", "vlanId": 30}]}),
          ("get", "/venues/v/dhcpConfigServiceProfiles/d1"): Resp(200, {"activeDhcpPoolNames": ["Guests"]}),
          ("get", "/venues/v/wifiDhcpPoolUsages"): Resp(200, {"wifiDhcpPoolUsages": [
              {"name": "Guests", "totalIpCount": 100, "usedIpCount": 7}]})})
pools = {p["name"]: p for p in pf.dhcp_pools(f, "t", "v")}
check("config + active + usage joined by name",
      pools["Guests"]["vlanId"] == 20 and pools["Guests"]["active"] is True and pools["Guests"]["usedIpCount"] == 7
      and pools["Spare"]["active"] is False and pools["Spare"]["usedIpCount"] is None, str(pools))
f = Fake({("post", "/dhcpConfigServiceProfiles/query"): Resp(200, {"data": [{"id": "d1", "venueIds": ["other"]}]})})
check("venue without AP DHCP: [] after one call", pf.dhcp_pools(f, "t", "v") == [] and len(f.calls) == 1)

print("4. Direct AP-group activation uses the supported PUTs")
import workflow.phases.activate_network_direct as dmod  # noqa: E402
seen = {}
async def fake_direct(**kw):
    seen.update(kw)
async def emit(*a, **k):
    pass
def phase(**inputs):
    ph = dmod.ActivateNetworkDirectPhase.__new__(dmod.ActivateNetworkDirectPhase)
    ph.r1_client = SimpleNamespace(venues=SimpleNamespace(activate_ssid_for_ap_group_direct=fake_direct))
    ph.tenant_id, ph.venue_id, ph.emit = "t", "v", emit
    base = dict(unit_id="u", unit_number="101", network_id="n", ap_group_id="g", ap_group_name="101")
    return asyncio.run(ph.execute(ph.Inputs(**{**base, **inputs})))
out = phase()
check("new SSID: straight to the group, not already on the venue, VLAN inherited, waits each step",
      seen.get("ap_group_id") == "g" and seen.get("already_activated") is False and seen.get("vlan_id") is None
      and seen.get("wait_for_completion") is True and out.activated, str(seen))
seen.clear(); phase(already_activated=True, is_venue_wide=True, ap_group_vlan="30")
check("venue-wide SSID: moved onto the group via .../settings, VLAN override 30",
      seen.get("already_activated") is True and seen.get("vlan_id") == 30, str(seen))
seen.clear(); out = phase(already_activated=True, is_venue_wide=False)
check("already on a specific group: nothing sent", seen == {} and out.already_active)
check("no /networkActivations anywhere in the phase", "networkActivations\"" not in open(dmod.__file__).read())

print("5. LAN port profile choice")
from services.sz_migration import lan_ports as lp  # noqa: E402
check("clash name fits R1's 32 characters",
      len(lp._clash_name("Client Visibility Ports", "Towers on West Franklin Street")) <= 32
      and lp._clash_name("Unit ports", "Lofts") == "Unit ports_Lofts")
check("SZ General written as SELECTIVE_TRUNK", lp._sig({"type": "GENERAL", "untagId": 5, "vlanMembers": "5,7"})[0]
      == "SELECTIVE_TRUNK")
profiles = [{"id": "d", "name": "Default Trunk Port (WAN)", "type": "TRUNK", "untagId": 1, "vlanMembers": "1-4094",
             "isDefault": True, "authType": "DISABLED", "venueActivations": [1, 2]},
            {"id": "x", "name": "Other", "type": "ACCESS", "untagId": 30, "vlanMembers": "30", "authType": "DISABLED"},
            {"id": "dot1x", "name": "Secure", "type": "ACCESS", "untagId": 40, "vlanMembers": "40", "authType": "PORT_BASED_AUTHENTICATOR"}]
ch = lp.ProfileChooser(None, "t", "V", [dict(p) for p in profiles], match_any=True)
notes = []
check("hand edit: reuses an identical plain profile whatever its name",
      asyncio.run(ch.choose({"portId": "1", "type": "ACCESS", "untagId": 30, "vlanMembers": "30"}, False, notes)) == "x")
try:
    asyncio.run(ch.choose({"portId": "1", "type": "ACCESS", "untagId": 40, "vlanMembers": "40"}, False, notes))
    check("hand edit: never matches an 802.1X profile, never creates", False)
except RuntimeError as e:
    check("hand edit: never matches an 802.1X profile, never creates", "create one in R1 first" in str(e), str(e))
check("port at default: R1's default profile",
      asyncio.run(ch.choose({"portId": "5", "type": "TRUNK", "untagId": 1, "vlanMembers": "1-4094"}, True, notes)) == "d")

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
