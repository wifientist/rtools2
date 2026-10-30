#!/usr/bin/env python3
"""
SZ Audit Basic mode: licenses and overall AP/switch counts.

WHAT THIS GUARDS
  1. Totals come from devicesSummary, online counts from inventory and the
     switch list, and the AP/switch license rows are matched exactly ("AP
     Split Tunnel Capacity License" is not the AP license).
  2. License types with nothing licensed and nothing used are left out.
  3. Perpetual licenses (expiry "292278994/08/17") are flagged perpetual and
     don't carry that as a date.
  4. Inventory pages that repeat a zone don't double count it, and a zone
     that never came back is reported rather than silently under-counting.
  5. A section that fails stays None with an error: never a count of 0.
  6. Switch list: a 404 (module off) means none, any other failure is an error.

Usage:
    docker compose exec backend python scripts/test_sz_basic_audit.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from routers.sz.basic_audit import fetch_basic_audit
from szapi.services.switches import SwitchService
from szapi.services.system import SystemService

SUMMARY = {"list": [
    {"licenseTypeDescription": "AP Split Tunnel Capacity License", "capacityControlLicenseCount": {"usedCount": 0, "totalCount": 5}},
    {"licenseTypeDescription": "AP Capacity License", "capacityControlLicenseCount": {"usedCount": 22, "totalCount": 50}},
    {"licenseTypeDescription": "Switch Capacity License", "capacityControlLicenseCount": {"usedCount": 3, "totalCount": 21}},
    {"licenseTypeDescription": "Feature License for URL Filtering", "capacityControlLicenseCount": {"usedCount": 0, "totalCount": 0}},
]}
LICENSES = {"list": [
    {"name": "CAPACITY-AP", "description": "AP license", "count": 49, "createTime": "2021/07/01", "expireDate": "292278994/08/17"},
    {"name": "CAPACITY-SWITCH", "description": "Switch license", "count": 20, "createTime": "2021/07/01", "expireDate": "2027/03/01"},
]}
DEVICES = {"totalAps": 24, "totalSwitches": 4}


class FakeClient:
    """Answers by endpoint; a queued Exception is raised instead of returned"""
    api_version = "v11_1"

    def __init__(self, responses):
        self.responses = responses

    async def _request(self, method, endpoint, use_root_url=False, **kwargs):
        answer = self.responses[endpoint.rsplit("/v11_1", 1)[-1]]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def client(**overrides):
    responses = {
        "/licensesSummary": SUMMARY,
        "/licenses": LICENSES,
        "/system/devicesSummary": DEVICES,
        "/system/inventory": {"totalCount": 3, "hasMore": False, "list": [
            {"zoneId": "a", "connectedAPs": 7},
            {"zoneId": "b", "connectedAPs": 3},
            {"zoneId": "c", "connectedAPs": 0},
        ]},
        "/switch": {"totalCount": 4, "hasMore": False, "list": [
            {"id": "1", "status": "ONLINE", "groupId": "g1"}, {"id": "2", "status": "ONLINE", "groupId": "g1"},
            {"id": "3", "status": "OFFLINE", "groupId": "g1"}, {"id": "4", "status": "OFFLINE", "groupId": "g2"},
        ]},
    }
    responses.update(overrides)
    fake = FakeClient(responses)
    fake.system = SystemService(fake)
    fake.switches = SwitchService(fake)
    return fake


CONTROLLER = SimpleNamespace(id=3, name="lab", sz_host="lab.example")


def audit(**overrides):
    return asyncio.run(fetch_basic_audit(client(**overrides), CONTROLLER))


def main():
    r = audit()
    assert (r.total_aps, r.aps_online, r.total_switches, r.switches_online) == (24, 10, 4, 2)
    assert (r.zones_with_online_aps, r.total_zones) == (2, 3)
    assert (r.switch_groups_with_online, r.switch_groups) == (1, 2)
    assert (r.ap_license.used, r.ap_license.total) == (22, 50)
    assert (r.switch_license.used, r.switch_license.total) == (3, 21)
    assert r.errors == []
    print("ok  totals, online counts, exact license match")

    types = [row.license_type for row in r.licenses]
    assert types == ["AP Split Tunnel Capacity License", "AP Capacity License", "Switch Capacity License"], types
    print("ok  empty license types left out")

    perpetual, dated = r.entitlements
    assert perpetual.perpetual and perpetual.expires is None
    assert not dated.perpetual and dated.expires == "2027-03-01" and dated.starts == "2021-07-01"
    assert dated.description == "Switch license"
    print("ok  perpetual expiry is not a date")

    r = audit(**{"/system/inventory": [
        {"totalCount": 3, "hasMore": True, "list": [{"zoneId": "a", "connectedAPs": 7}, {"zoneId": "b", "connectedAPs": 3}]},
        {"totalCount": 3, "hasMore": False, "list": [{"zoneId": "b", "connectedAPs": 3}]},
    ]})
    assert r.aps_online == 10, r.aps_online
    assert any("2 of 3 zones" in e for e in r.errors), r.errors
    print("ok  repeated zone not double counted, missed zone reported")

    r = audit(**{
        "/licensesSummary": ValueError("SmartZone API error (403): nope"),
        "/system/devicesSummary": ValueError("boom"),
        "/switch": ValueError("SmartZone API error (500): down"),
    })
    assert r.ap_license is None and r.total_aps is None and r.switches_online is None
    assert r.aps_online == 10 and len(r.entitlements) == 2
    assert len(r.errors) == 3, r.errors
    print("ok  failed sections stay None with an error, others still read")

    r = audit(**{"/switch": ValueError("SmartZone API error (404): not found")})
    assert r.switches_online == 0 and r.errors == []
    print("ok  switch module off means none")

    print("\nAll passed")


if __name__ == "__main__":
    main()
